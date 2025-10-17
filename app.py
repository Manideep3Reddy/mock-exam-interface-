# app.py
"""
Streamlit Mock Exam App (single-file) — bilingual PDF aware

Run:
  pip install streamlit pdfplumber reportlab
  streamlit run app.py
"""

import streamlit as st
import pdfplumber
import re
import time
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from io import BytesIO

st.set_page_config(page_title="Mock Exam Interface", layout="wide")
st.title("📘 Mock Exam Interface (MCQ) — Bilingual PDF aware")

# ---------- Helper functions ----------

def is_page_hindi(text, threshold=0.20):
    """Return True if fraction of Devanagari chars in text exceeds threshold."""
    if not text:
        return False
    total = len(text)
    devanagari = sum(1 for ch in text if '\u0900' <= ch <= '\u097F')
    frac = devanagari / max(1, total)
    return frac >= threshold

def extract_pages_text(file):
    """Return list of page texts from the PDF (in order). Uses pdfplumber."""
    texts = []
    try:
        with pdfplumber.open(file) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text() or ""
                texts.append(page_text)
    except Exception as e:
        st.error(f"Error reading PDF: {e}")
    return texts

def extract_english_text_from_bilingual_pdf(file, assume_alternating=True, first_page_is_hindi=True):
    """
    If assume_alternating: keep every 2nd page that corresponds to English pages.
    Else: keep pages where Devanagari fraction is low.
    """
    pages = extract_pages_text(file)
    if not pages:
        return ""
    english_pages = []
    if assume_alternating:
        # If first page is Hindi, then English pages are indexes 1,3,5,... (0-based)
        start_idx = 1 if first_page_is_hindi else 0
        for i in range(start_idx, len(pages), 2):
            if pages[i]:
                english_pages.append(pages[i])
    else:
        for p in pages:
            if not is_page_hindi(p):
                english_pages.append(p)
    # join pages with double newlines to keep separation
    return "\n\n".join(english_pages)

def parse_mcqs_from_text(text):
    """Return list of {qnum, question, options: [..]}"""
    if not text:
        return []

    # Normalize newlines
    text = text.replace('\r', '\n')

    # Split by question numbers like: Q.1) or Q1) or 1. or 1)
    parts = re.split(r"\n\s*(?=(?:Q\.?\s*)?\d{1,3}[\.)])", text)
    questions = []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        # Remove leading 'Q.' if present
        p_clean = re.sub(r'^(Q\.?\s*)', '', p, flags=re.IGNORECASE)
        m = re.match(r"^(\d{1,3})[\.)]\s*(.*)$", p_clean, re.DOTALL)
        if not m:
            continue
        qnum = m.group(1)
        body = m.group(2).strip()
        # Try to split options by lines that start with A. A) 1. 1)
        lines = [ln for ln in body.split('\n') if ln.strip()]
        option_lines = [ln.strip() for ln in lines if re.match(r'^(?:[A-Da-d][\.|\)]|[1-4][\.|\)])', ln.strip())]
        if option_lines:
            # Extract question text as everything before the first option line
            first_opt_idx = next(i for i, ln in enumerate(lines) if re.match(r'^(?:[A-Da-d][\.|\)]|[1-4][\.|\)])', ln.strip()))
            qtext = ' '.join(lines[:first_opt_idx]).strip()
            opts = []
            for ln in lines[first_opt_idx:]:
                m_opt = re.match(r'^(?:([A-Da-d]|[1-4]))[\.|\)]\s*(.*)$', ln.strip())
                if m_opt:
                    opts.append(m_opt.group(2).strip())
            # If fewer than 2 options found, try inline splitting
            if len(opts) < 2:
                inline_parts = re.split(r'(?=[A-D][\.|\)])', body)
                inline_opts = []
                for ip in inline_parts[1:]:
                    m2 = re.match(r'^[A-D][\.|\)]\s*(.*)$', ip.strip(), re.DOTALL)
                    if m2:
                        inline_opts.append(m2.group(1).strip())
                if inline_opts:
                    qtext = inline_parts[0].strip()
                    opts = inline_opts
        else:
            # No clear option lines: attempt to find options like ' (A) option '
            inline_opts = re.findall(r'\(?([A-Da-d]|[1-4])\)?[\.|\)]?\s*([^\n\r]+)', body)
            if inline_opts and len(inline_opts) >= 2:
                qtext = re.split(r'\(?[A-Da-d]|[1-4]\)?[\.|\)]?', body)[0].strip()
                opts = [t[1].strip() for t in inline_opts]
            else:
                # Give up — treat whole body as question with no options
                qtext = body
                opts = []

        questions.append({
            'qnum': qnum,
            'question': qtext,
            'options': opts
        })
    return questions

def parse_answer_key_text(text):
    mapping = {}
    if not text:
        return mapping
    lines = [ln.strip() for ln in text.split('\n') if ln.strip()]
    for ln in lines:
        m = re.match(r'^(\d{1,3})\s*[-:\.)]?\s*([A-Da-d1-4])$', ln)
        if m:
            q = m.group(1)
            a = m.group(2).upper()
            if a in '1234':
                a = {'1':'A','2':'B','3':'C','4':'D'}[a]
            mapping[q] = a
        else:
            m2 = re.match(r'^(\d{1,3})\s*[-:\.)]?\s*(.+)$', ln)
            if m2:
                q = m2.group(1)
                val = m2.group(2).strip()
                mapping[q] = val
    return mapping

def evaluate_responses(questions, user_answers, answer_key, negative_mark=0.0, marks_per_q=1.0):
    total = 0.0
    correct = 0
    incorrect = 0
    details = []
    for q in questions:
        qn = q['qnum']
        correct_ans = answer_key.get(qn)
        user_ans = user_answers.get(qn)
        # If answer_key has option text, attempt to find matching option letter
        if correct_ans and correct_ans not in ['A','B','C','D']:
            for idx, opt in enumerate(q['options']):
                if correct_ans.lower() in opt.lower():
                    correct_ans = ['A','B','C','D'][idx]
                    break
        is_correct = False
        if correct_ans and user_ans:
            try:
                is_correct = (correct_ans.upper() == user_ans.upper())
            except Exception:
                is_correct = False
        if is_correct:
            total += marks_per_q
            correct += 1
        else:
            if user_ans:
                total -= negative_mark
                incorrect += 1
        details.append({'qnum':qn, 'correct': correct_ans, 'user': user_ans, 'is_correct': is_correct})
    return total, correct, incorrect, details

def generate_result_pdf(student_name, exam_title, details, total_score, out_buffer: BytesIO):
    c = canvas.Canvas(out_buffer, pagesize=A4)
    width, height = A4
    c.setFont('Helvetica-Bold', 14)
    c.drawString(40, height - 40, f"Exam: {exam_title}")
    c.setFont('Helvetica', 12)
    c.drawString(40, height - 60, f"Student: {student_name}")
    c.drawString(40, height - 80, f"Score: {total_score}")
    y = height - 110
    for d in details:
        line = f"Q{d['qnum']}: your={d['user']} correct={d['correct']} {'✔' if d['is_correct'] else '✖'}"
        c.drawString(40, y, line)
        y -= 18
        if y < 60:
            c.showPage()
            y = height - 40
    c.save()
    out_buffer.seek(0)
    return out_buffer

# ---------- UI ----------

with st.sidebar:
    st.header("Exam Settings")
    exam_title = st.text_input("Exam title", value="Mock Exam")
    duration_min = st.number_input("Duration (minutes)", min_value=1, max_value=600, value=60)
    marks_per_q = st.number_input("Marks per correct answer", value=1.0, step=0.5)
    negative_mark = st.number_input("Negative marks per wrong answer", value=0.0, step=0.25)
    show_one_by_one = st.checkbox("Show one question per page", value=False)

st.info("Upload question PDF (text-based, not scanned). Upload answer-key PDF or paste the key. If your PDF has alternating Hindi/English pages (first page Hindi), leave the default setting enabled.")
qfile = st.file_uploader("Question PDF (E+H) — bilingual or English-only", type=['pdf'])
ansfile = st.file_uploader("Answer Key PDF (optional)", type=['pdf'])
manual_key = st.text_area("Or paste answer key (one per line, e.g. '1 A')", height=120)

col1, col2 = st.columns([1,1])
with col1:
    student_name = st.text_input("Student name")
with col2:
    start_button = st.button("Start Exam")

# bilingual options
st.sidebar.markdown("---")
st.sidebar.subheader("Bilingual PDF handling")
assume_alt = st.sidebar.checkbox("PDF has strictly alternating Hindi/English pages", value=True)
first_page_hindi = st.sidebar.checkbox("First page is Hindi (instructions)", value=True)
st.sidebar.caption("If unsure, uncheck 'alternating' and the app will detect Hindi pages per-page using Devanagari detection.")

# Initialize session state
if 'questions' not in st.session_state:
    st.session_state['questions'] = []
if 'user_answers' not in st.session_state:
    st.session_state['user_answers'] = {}
if 'answer_key' not in st.session_state:
    st.session_state['answer_key'] = {}
if 'start_time' not in st.session_state:
    st.session_state['start_time'] = None
if 'end_time' not in st.session_state:
    st.session_state['end_time'] = None

# Load and extract English pages when uploaded
if qfile and (not st.session_state['questions']):
    st.info("Extracting English pages from bilingual PDF...")
    eng_text = extract_english_text_from_bilingual_pdf(qfile, assume_alternating=assume_alt, first_page_is_hindi=first_page_hindi)
    parsed = parse_mcqs_from_text(eng_text)
    st.session_state['questions'] = parsed
    st.success(f"Parsed {len(parsed)} questions from English pages. Please verify and edit if needed.")

# parse answer key
if ansfile and (not st.session_state['answer_key']):
    atext = extract_english_text_from_bilingual_pdf(ansfile, assume_alternating=False)
    key_map = parse_answer_key_text(atext)
    st.session_state['answer_key'] = key_map
    if key_map:
        st.success("Parsed answer key from uploaded PDF.")

if manual_key:
    key_map2 = parse_answer_key_text(manual_key)
    st.session_state['answer_key'].update(key_map2)

# Start exam
if start_button:
    if not st.session_state['questions']:
        st.warning("Please upload a question PDF and wait for parsing before starting.")
    else:
        st.session_state['start_time'] = time.time()
        st.session_state['end_time'] = st.session_state['start_time'] + duration_min * 60
        st.success("Exam started. Good luck!")

# Display timer
if st.session_state['start_time']:
    remaining = int(st.session_state['end_time'] - time.time())
    if remaining < 0:
        remaining = 0
    mins = remaining // 60
    secs = remaining % 60
    st.markdown(f"**Time remaining:** {mins:02d}:{secs:02d}")

# Show questions
questions = st.session_state['questions']
if questions:
    with st.form(key='exam_form'):
        if show_one_by_one:
            if 'page' not in st.session_state:
                st.session_state['page'] = 0
            qidx = st.session_state['page']
            q = questions[qidx]
            st.write(f"**Q{q['qnum']}**. {q['question']}")
            options = q['options']
            opt_labels = ['A','B','C','D']
            if options and len(options) > 0:
                display_choices = []
                for i,opt in enumerate(options):
                    label = opt_labels[i] if i < len(opt_labels) else str(i+1)
                    display_choices.append(f"{label}. {opt}")
                default_index = 0
                if q['qnum'] in st.session_state['user_answers']:
                    try:
                        default_index = ['A','B','C','D'].index(st.session_state['user_answers'][q['qnum']])
                    except Exception:
                        default_index = 0
                choice = st.radio("Choose", display_choices, index=default_index)
                selected_letter = choice.split('.')[0].strip()
                st.session_state['user_answers'][q['qnum']] = selected_letter
            else:
                ans_text = st.text_area("Answer (no options detected)")
                st.session_state['user_answers'][q['qnum']] = ans_text
            coln1, coln2, coln3 = st.columns(3)
            with coln1:
                if st.button("Previous"):
                    st.session_state['page'] = max(0, st.session_state['page'] - 1)
                    st.experimental_rerun()
            with coln2:
                if st.button("Next"):
                    st.session_state['page'] = min(len(questions)-1, st.session_state['page'] + 1)
                    st.experimental_rerun()
            with coln3:
                submit_btn = st.form_submit_button("Submit Exam")
        else:
            for q in questions:
                st.write(f"**Q{q['qnum']}**. {q['question']}")
                options = q['options']
                opt_labels = ['A','B','C','D']
                if options and len(options) > 0:
                    choices = []
                    for i,op in enumerate(options):
                        label = opt_labels[i] if i < 4 else str(i+1)
                        choices.append(f"{label}. {op}")
                    sel = st.radio(f"Q{q['qnum']}", choices, key=f"q_{q['qnum']}")
                    sel_letter = sel.split('.')[0].strip()
                    st.session_state['user_answers'][q['qnum']] = sel_letter
                else:
                    ans_text = st.text_area(f"Q{q['qnum']} answer (no options detected)", key=f"free_{q['qnum']}")
                    st.session_state['user_answers'][q['qnum']] = ans_text
            submit_btn = st.form_submit_button("Submit Exam")

    if submit_btn:
        if st.session_state['end_time'] and time.time() > st.session_state['end_time']:
            st.warning("Time is over. Submission recorded but exam time exceeded.")
        total, correct, incorrect, details = evaluate_responses(questions, st.session_state['user_answers'], st.session_state['answer_key'], negative_mark=negative_mark, marks_per_q=marks_per_q)
        st.success(f"Score: {total} | Correct: {correct} | Incorrect: {incorrect}")
        with st.expander("Per-question details"):
            for d in details:
                st.write(f"Q{d['qnum']}: your={d['user']} correct={d['correct']} -> {'Correct' if d['is_correct'] else 'Wrong'}")
        buf = BytesIO()
        generate_result_pdf(student_name or 'Student', exam_title, details, total, buf)
        st.download_button("Download result PDF", data=buf, file_name=f"{exam_title.replace(' ','_')}_result.pdf", mime='application/pdf')

else:
    st.info("Upload a question PDF to begin. Use the sidebar to control bilingual handling if needed.")

st.markdown("---")
st.caption("Built for quick mock exams. If parsing isn't perfect, paste/edit the answer key manually. For very large or scanned PDFs, consider OCR first (e.g., using Tesseract) and then upload the extracted text.")
