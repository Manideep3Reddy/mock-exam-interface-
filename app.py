"""
Streamlit Mock Exam App (single-file)

How to run (locally):
1. Create a virtual env and install dependencies:
   pip install streamlit pdfplumber reportlab

2. Run:
   streamlit run streamlit_mock_exam_app.py

Notes:
- This app tries to parse MCQs from a question PDF (text-based PDF).
- Answer key can be uploaded as a PDF or pasted as text in the format:
    1 A
    2 B
  or
    1-A
    2-B
  or
    1:A

- This is intended for quick deployment on Streamlit Cloud or locally. If you deploy to Streamlit Cloud, add a requirements.txt with the packages above.

"""

import streamlit as st
import pdfplumber
import re
import time
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from io import BytesIO

st.set_page_config(page_title="Mock Exam Interface", layout="wide")
st.title("📘 Mock Exam Interface (MCQ)")

# ---------- Helper functions ----------

def extract_text_from_pdf_file(file) -> str:
    try:
        text = ""
        with pdfplumber.open(file) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text()
                if page_text:
                    text += "\n" + page_text
        return text
    except Exception as e:
        st.error(f"Error reading PDF: {e}")
        return ""


def parse_mcqs_from_text(text):
    """Return list of {qnum, question, options: [..]}"""
    if not text:
        return []

    # Normalize newlines
    text = text.replace('\r', '\n')

    # Split by question numbers like: 1.  or 1)  or newline + 1
    parts = re.split(r"\n\s*(?=\d{1,3}[\.)])", text)
    questions = []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        m = re.match(r"^(\d{1,3})[\.)]\s*(.*)$", p, re.DOTALL)
        if not m:
            continue
        qnum = m.group(1)
        body = m.group(2).strip()
        # Try to split options by lines that start with A. A) 1. 1)
        lines = body.split('\n')
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
            # If fewer than 4 options found but options may be inline; try inline split by ' A. ' etc
            if len(opts) < 2:
                # try inline splitting by ' A. ' or ' A) '
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
    """Expect lines like '1 A' or '1-A' or '1:A' or '1. A' or '1) A' or '1 - 1' (numbers)
       Returns dict qnum -> letter (A/B/C/D) or index (1-4)"""
    mapping = {}
    if not text:
        return mapping
    lines = [ln.strip() for ln in text.split('\n') if ln.strip()]
    for ln in lines:
        m = re.match(r'^(\d{1,3})\s*[-:\.)]?\s*([A-Da-d1-4])$', ln)
        if m:
            q = m.group(1)
            a = m.group(2).upper()
            # normalize numbers to letters
            if a in '1234':
                a = {'1':'A','2':'B','3':'C','4':'D'}[a]
            mapping[q] = a
        else:
            # try pattern '1 - Option text' -> attempt to map by option text later
            m2 = re.match(r'^(\d{1,3})\s*[-:\.)]?\s*(.+)$', ln)
            if m2:
                q = m2.group(1)
                val = m2.group(2).strip()
                mapping[q] = val  # could be text; we'll try to match later
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
            # try to match by option text
            for idx, opt in enumerate(q['options']):
                if correct_ans.lower() in opt.lower():
                    correct_ans = ['A','B','C','D'][idx]
                    break
        is_correct = False
        if correct_ans and user_ans:
            is_correct = (correct_ans.upper() == user_ans.upper())
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

st.info("Upload question PDF (text-based, not scanned). Upload answer key PDF or paste the key.")
qfile = st.file_uploader("Question PDF", type=['pdf'])
ansfile = st.file_uploader("Answer Key PDF (optional)", type=['pdf'])
manual_key = st.text_area("Or paste answer key (one per line, e.g. '1 A')", height=120)

col1, col2 = st.columns([1,1])
with col1:
    student_name = st.text_input("Student name")
with col2:
    start_button = st.button("Start Exam")

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

# Load PDFs when uploaded
if qfile and (not st.session_state['questions']):
    qtext = extract_text_from_pdf_file(qfile)
    parsed = parse_mcqs_from_text(qtext)
    st.session_state['questions'] = parsed
    st.success(f"Parsed {len(parsed)} questions (parsed question count). Please verify.")

# parse answer key
if ansfile and (not st.session_state['answer_key']):
    atext = extract_text_from_pdf_file(ansfile)
    key_map = parse_answer_key_text(atext)
    st.session_state['answer_key'] = key_map
    if key_map:
        st.success("Parsed answer key from uploaded PDF.")

if manual_key:
    key_map2 = parse_answer_key_text(manual_key)
    # merge
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
            # simple pagination
            if 'page' not in st.session_state:
                st.session_state['page'] = 0
            qidx = st.session_state['page']
            q = questions[qidx]
            st.write(f"**Q{q['qnum']}**. {q['question']}")
            options = q['options']
            # display options or placeholders
            opt_labels = ['A','B','C','D']
            if options and len(options) > 0:
                choice = st.radio("Choose", [f"{opt_labels[i]}. {options[i]}" for i in range(len(options))], index=0 if q['qnum'] not in st.session_state['user_answers'] else ['A','B','C','D'].index(st.session_state['user_answers'][q['qnum']]))
                # store selection as letter
                selected_letter = choice.split('.')[0].strip()
                st.session_state['user_answers'][q['qnum']] = selected_letter
            else:
                st.text_area("Answer (no options detected)")
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
                    default_idx = 0
                    if q['qnum'] in st.session_state['user_answers']:
                        try:
                            default_idx = ['A','B','C','D'].index(st.session_state['user_answers'][q['qnum']])
                        except Exception:
                            default_idx = 0
                    sel = st.radio(f"Q{q['qnum']}", choices, key=f"q_{q['qnum']}")
                    sel_letter = sel.split('.')[0].strip()
                    st.session_state['user_answers'][q['qnum']] = sel_letter
                else:
                    ans_text = st.text_area(f"Q{q['qnum']} answer (no options detected)", key=f"free_{q['qnum']}")
                    st.session_state['user_answers'][q['qnum']] = ans_text
            submit_btn = st.form_submit_button("Submit Exam")

    if submit_btn:
        # check time
        if st.session_state['end_time'] and time.time() > st.session_state['end_time']:
            st.warning("Time is over. Submission recorded but exam time exceeded.")
        # evaluate
        total, correct, incorrect, details = evaluate_responses(questions, st.session_state['user_answers'], st.session_state['answer_key'], negative_mark=negative_mark, marks_per_q=marks_per_q)
        st.success(f"Score: {total} | Correct: {correct} | Incorrect: {incorrect}")
        # show per question
        with st.expander("Per-question details"):
            for d in details:
                st.write(f"Q{d['qnum']}: your={d['user']} correct={d['correct']} -> {'Correct' if d['is_correct'] else 'Wrong'}")
        # allow PDF download
        buf = BytesIO()
        generate_result_pdf(student_name or 'Student', exam_title, details, total, buf)
        st.download_button("Download result PDF", data=buf, file_name=f"{exam_title.replace(' ','_')}_result.pdf", mime='application/pdf')

else:
    st.info("Upload a question PDF to begin.")

st.markdown("---")
st.caption("Built for quick mock exams. If parsing isn't perfect, you can paste the answer key manually. For large-scale or live-timed exams, consider integrating a JS timer component and more robust PDF parsing.")
