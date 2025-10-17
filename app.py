# app.py
"""
Streamlit Mock Exam App — tailored for two-column bilingual PDFs (English pages alternate)
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
st.title("📘 Mock Exam Interface — Two-column bilingual-aware (100 Qs)")

# ---------- Helpers for PDF extraction/parsing ----------

def extract_pages_text(file):
    pages = []
    try:
        with pdfplumber.open(file) as pdf:
            for page in pdf.pages:
                pages.append(page)
    except Exception as e:
        st.error(f"Error opening PDF: {e}")
    return pages

def extract_english_columns_text(file, first_page_hindi=True):
    """
    Expect strictly alternating pages: odd pages Hindi, even pages English (1-based counting).
    This function extracts English pages only, splits each English page into left & right halves,
    and returns a single concatenated text where each column is appended in order (left then right).
    """
    pages = extract_pages_text(file)
    if not pages:
        return ""
    texts = []
    # Pages list is 0-based; if first_page_hindi True, English pages are indices 1,3,5...
    start_idx = 1 if first_page_hindi else 0
    for i in range(start_idx, len(pages), 2):
        page = pages[i]
        # compute midpoint for vertical split
        width = page.width
        height = page.height
        mid_x = width / 2.0
        # left column bbox: (x0, y0, x1, y1) — pdfplumber coordinates: (0,0) bottom-left
        left_bbox = (0, 0, mid_x, height)
        right_bbox = (mid_x, 0, width, height)
        try:
            left_text = (page.crop(left_bbox).extract_text() or "").strip()
        except Exception:
            left_text = ""
        try:
            right_text = (page.crop(right_bbox).extract_text() or "").strip()
        except Exception:
            right_text = ""
        # Append left then right (this preserves typical reading order)
        if left_text:
            texts.append(left_text)
        if right_text:
            texts.append(right_text)
    # join by two newlines so parsing sees separation
    return "\n\n".join(texts)

def parse_mcqs_from_column_text(text):
    """
    Parse questions from single-column text. Returns list of dicts: {qnum, question, options:list}.
    Recognizes question numbers like 'Q.1)', '1)', '1.' at line starts.
    Recognizes option lines that *start* with lowercase a/b/c/d + ')' or '.' or uppercase A/B/C/D as well.
    Ignores inline parentheses letters not at line starts.
    """
    if not text:
        return []
    # Normalise
    text = text.replace('\r', '\n')
    # Ensure splits occur when a new question number appears at line start
    parts = re.split(r"\n\s*(?=(?:Q\.?\s*)?\d{1,3}[\.)])", text)
    questions = []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        p_clean = re.sub(r'^(Q\.?\s*)', '', p, flags=re.IGNORECASE)
        m = re.match(r"^(\d{1,3})[\.)]\s*(.*)$", p_clean, re.DOTALL)
        if not m:
            continue
        qnum = m.group(1).strip()
        body = m.group(2).strip()
        # Split body into lines and find option lines that start a line
        lines = [ln for ln in body.split('\n') if ln.strip()]
        opts = []
        q_lines = []
        option_line_idx = None
        for idx, ln in enumerate(lines):
            if re.match(r'^[\s]*([a-dA-D]|[1-4])[\.\)]\s+', ln):
                option_line_idx = idx
                break
        if option_line_idx is not None:
            qtext = ' '.join(lines[:option_line_idx]).strip()
            for ln in lines[option_line_idx:]:
                mopt = re.match(r'^[\s]*([a-dA-D]|[1-4])[\.\)]\s+(.*)$', ln)
                if mopt:
                    label = mopt.group(1).upper()
                    opttext = mopt.group(2).strip()
                    # normalize labels to A-D
                    if label in ['1','2','3','4']:
                        label = {'1':'A','2':'B','3':'C','4':'D'}[label]
                    opts.append((label, opttext))
                else:
                    # Some option text may flow to next line; attach to last option if exists
                    if opts:
                        opts[-1] = (opts[-1][0], opts[-1][1] + ' ' + ln.strip())
                    else:
                        # fallback — treat as part of question
                        qtext += ' ' + ln.strip()
        else:
            # No explicit starting option lines — try inline pattern A. option or (a) option
            inline_opts = re.findall(r'\(?([a-dA-D])\)?[\.\)]\s*([^;\\n]+)', body)
            if inline_opts and len(inline_opts) >= 2:
                # question text is before first inline option
                first_split = re.split(r'\(?[a-dA-D]\)?[\.\)]', body, maxsplit=1)
                qtext = first_split[0].strip()
                opts = [(lab.upper(), txt.strip()) for lab, txt in inline_opts]
            else:
                qtext = body
                opts = []
        # Sort opts by label A-D (in case PDF order is slightly off)
        opts_sorted = sorted(opts, key=lambda x: 'ABCD'.index(x[0]) if x and x[0] in 'ABCD' else 99)
        opts_texts = [t[1] for t in opts_sorted]
        # map qnum to ensure numeric up to 100
        questions.append({'qnum': qnum, 'question': qtext, 'options': opts_texts})
    return questions

def parse_all_columns_to_questions(full_text):
    """
    full_text is concatenation of column texts in order (left col1, right col1, left col2, right col2...)
    We'll parse each column separately and then combine preserving order.
    """
    if not full_text:
        return []
    column_texts = re.split(r'\n\s*\n\s*\n', full_text)  # attempt to split big gaps between columns
    # fallback: split by double newline if triple newline didn't appear
    if len(column_texts) == 1:
        column_texts = full_text.split('\n\n')
    all_questions = []
    for col_txt in column_texts:
        col_txt = col_txt.strip()
        if not col_txt:
            continue
        qlist = parse_mcqs_from_column_text(col_txt)
        # append preserving column order
        for q in qlist:
            all_questions.append(q)
            if len(all_questions) >= 100:
                return all_questions[:100]
    return all_questions[:100]

def parse_answer_key_from_solution_pdf(file):
    """
    Parse answer key from solutions PDF.
    This scans for patterns like:
      Q.1)  Ans) d
      1) Ans) d
      Ans) d
      1 d
    Returns dict qnum -> letter (A-D)
    """
    pages = extract_pages_text(file)
    text_all = []
    for p in pages:
        try:
            text_all.append(p.extract_text() or "")
        except Exception:
            text_all.append("")
    text = "\n".join(text_all)
    mapping = {}
    # first try to find explicit 'Q.* Ans) X' patterns
    matches = re.findall(r'Q\.?\s*(\d{1,3})[\)\.\s]*[^\n]{0,40}?Ans\)?\s*[:\-]?\s*([a-dA-D])', text)
    for m in matches:
        qnum = m[0]
        ans = m[1].upper()
        mapping[qnum] = ans
    # fallback: lines with 'Ans) x' where question number above or previous
    if not mapping:
        # find all patterns '(\d{1,3}).*Ans) x'
        matches2 = re.findall(r'(\d{1,3})[\)\.\s].{0,60}?Ans\)?\s*[:\-]?\s*([a-dA-D])', text)
        for m in matches2:
            mapping[m[0]] = m[1].upper()
    # ultimate fallback: any 'Ans) x' sequentially map to question numbers found in order
    if not mapping:
        ans_seq = re.findall(r'Ans\)?\s*[:\-]?\s*([a-dA-D])', text)
        # find qnums in text order
        qnums = re.findall(r'\b(\d{1,3})[\)\.]\s', text)
        # map sequentially
        for i, ans in enumerate(ans_seq):
            if i < len(qnums):
                mapping[qnums[i]] = ans.upper()
    # final cleanup normalize numbers to strings
    # Also try matching '1) d' patterns
    more = re.findall(r'(\d{1,3})[\)\.]\s*([a-dA-D])', text)
    for m in more:
        if m[0] not in mapping:
            mapping[m[0]] = m[1].upper()
    return mapping

# ---------- Evaluation & PDF generation ----------

def evaluate_responses(questions, user_answers, answer_key, negative_mark=0.0, marks_per_q=1.0):
    total = 0.0
    correct = 0
    incorrect = 0
    details = []
    for q in questions:
        qn = q['qnum']
        correct_ans = answer_key.get(qn)
        user_ans = user_answers.get(qn)
        # If answer_key contains text (not letter), try to match option text
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
        details.append({'qnum': qn, 'question': q['question'], 'correct': correct_ans, 'user': user_ans, 'is_correct': is_correct})
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

# ---------- Streamlit UI ----------

with st.sidebar:
    st.header("Exam settings")
    exam_title = st.text_input("Exam title", value="Mock Exam")
    duration_min = st.number_input("Duration (minutes)", min_value=1, max_value=600, value=120)
    marks_per_q = st.number_input("Marks per correct answer", value=2.0, step=0.5)
    negative_mark = st.number_input("Negative marks per wrong answer", value=0.66, step=0.01)
    show_one_by_one = st.checkbox("Show one question per page", value=False)

st.info("Upload bilingual question PDF (E+H, two-column English pages) and the English solution PDF. Then verify parsed questions before starting the exam.")
qfile = st.file_uploader("Question PDF (E+H)", type=["pdf"])
solfile = st.file_uploader("Solution PDF (ENG)", type=["pdf"])

col1, col2 = st.columns(2)
with col1:
    student_name = st.text_input("Student name")
with col2:
    start_button = st.button("Start Exam (after verifying parsed questions)")

# session-state
if 'questions' not in st.session_state:
    st.session_state['questions'] = []
if 'answer_key' not in st.session_state:
    st.session_state['answer_key'] = {}
if 'user_answers' not in st.session_state:
    st.session_state['user_answers'] = {}
if 'start_time' not in st.session_state:
    st.session_state['start_time'] = None
if 'end_time' not in st.session_state:
    st.session_state['end_time'] = None

# Step A: Extract + parse when files uploaded
if qfile and (not st.session_state['questions']):
    st.info("Extracting English columns from bilingual PDF (assumes first page is Hindi and pages strictly alternate)...")
    full_text = extract_english_columns_text(qfile, first_page_hindi=True)
    raw_questions = parse_all_columns_to_questions(full_text)
    # ensure 100 questions by limiting (or warn if less)
    if len(raw_questions) < 100:
        st.warning(f"Parser found {len(raw_questions)} questions. Please verify and edit below. (The exam expects 100 questions.)")
    else:
        st.success(f"Parsed {len(raw_questions)} questions (showing first 100).")
    # keep first 100 only
    st.session_state['questions'] = raw_questions[:100]

if solfile and (not st.session_state['answer_key']):
    st.info("Parsing solution PDF to build answer key...")
    key_map = parse_answer_key_from_solution_pdf(solfile)
    if not key_map:
        st.warning("Could not parse an answer map automatically from the solution PDF — please paste the answer key manually in 'Manual key' below.")
    else:
        st.success(f"Parsed {len(key_map)} answers from solution PDF.")
    st.session_state['answer_key'] = key_map

# Manual key input
manual_key = st.text_area("Manual key (optional) — one per line, e.g. '1 A' or '1 - A'. Use this to fix or supply missing answers.", height=120)
if manual_key:
    for ln in manual_key.splitlines():
        ln = ln.strip()
        if not ln:
            continue
        m = re.match(r'(\d{1,3}).?[-:\)]?\s*([A-Da-d1-4])$', ln)
        if m:
            q = m.group(1)
            a = m.group(2).upper()
            if a in '1234':
                a = {'1':'A','2':'B','3':'C','4':'D'}[a]
            st.session_state['answer_key'][q] = a
        else:
            m2 = re.match(r'(\d{1,3}).?[-:\)]?\s*(.+)$', ln)
            if m2:
                st.session_state['answer_key'][m2.group(1)] = m2.group(2).strip()

# Show parsed questions and allow edits (important)
if st.session_state['questions']:
    st.markdown("### Parsed questions (edit if parsing errors occurred). Editing here will change what exam uses.")
    edited_questions = []
    for q in st.session_state['questions']:
        qid = q['qnum']
        with st.expander(f"Q{qid}: {q['question'][:80]}...", expanded=False):
            new_qtext = st.text_area(f"Question {qid} text", value=q['question'], key=f"qtext_{qid}")
            # show options with editable fields
            opts = q.get('options', [])
            # ensure up to 4 option fields
            while len(opts) < 4:
                opts.append("")
            new_opts = []
            for i in range(4):
                new_opt = st.text_input(f"Q{qid} option {['A','B','C','D'][i]}", value=opts[i], key=f"opt_{qid}_{i}")
                new_opts.append(new_opt)
            edited_questions.append({'qnum': qid, 'question': new_qtext, 'options': new_opts})
    # replace
    st.session_state['questions'] = edited_questions

# Show brief answer key preview and mismatch check
if st.session_state['answer_key']:
    st.markdown("### Answer key preview (from solution PDF + manual edits)")
    st.write(st.session_state['answer_key'])

# Cross-check numbering
if st.session_state['questions'] and st.session_state['answer_key']:
    qnums_parsed = set(q['qnum'] for q in st.session_state['questions'])
    qnums_key = set(st.session_state['answer_key'].keys())
    missing_in_key = sorted([q for q in qnums_parsed if q not in qnums_key], key=lambda x:int(x))
    missing_in_questions = sorted([q for q in qnums_key if q not in qnums_parsed], key=lambda x:int(x))
    if missing_in_key:
        st.warning(f"{len(missing_in_key)} parsed questions are missing answers in key (examples): {missing_in_key[:10]}")
    if missing_in_questions:
        st.warning(f"{len(missing_in_questions)} answers in key don't match parsed question numbers (examples): {missing_in_questions[:10]}")

# Start exam
if start_button:
    if not st.session_state['questions']:
        st.error("No parsed questions available — upload QP first and fix parsing.")
    else:
        st.session_state['start_time'] = time.time()
        st.session_state['end_time'] = st.session_state['start_time'] + duration_min * 60
        st.success("Exam started — timer running.")

# Timer display
if st.session_state.get('start_time'):
    rem = int(st.session_state['end_time'] - time.time())
    if rem < 0:
        rem = 0
    st.markdown(f"**Time remaining:** {rem//60:02d}:{rem%60:02d}")

# Show exam form
questions = st.session_state.get('questions', [])
if questions:
    with st.form("exam_form"):
        if show_one_by_one:
            if 'page' not in st.session_state:
                st.session_state['page'] = 0
            idx = st.session_state['page']
            q = questions[idx]
            st.write(f"**Q{q['qnum']}**. {q['question']}")
            opts = q['options']
            labels = ['A','B','C','D']
            choices = []
            for i, o in enumerate(opts):
                if o and o.strip():
                    choices.append(f"{labels[i]}. {o}")
            if not choices:
                ans_text = st.text_area("Answer text (no options detected)")
                st.session_state['user_answers'][q['qnum']] = ans_text
            else:
                sel = st.radio("Choose", choices, key=f"sel_{q['qnum']}")
                st.session_state['user_answers'][q['qnum']] = sel.split('.')[0].strip()
            c1,c2,c3 = st.columns(3)
            with c1:
                if st.form_submit_button("Previous"):
                    st.session_state['page'] = max(0, st.session_state['page'] - 1)
                    st.experimental_rerun()
            with c2:
                if st.form_submit_button("Next"):
                    st.session_state['page'] = min(len(questions)-1, st.session_state['page'] + 1)
                    st.experimental_rerun()
            with c3:
                submit_btn = st.form_submit_button("Submit Exam")
        else:
            for q in questions:
                st.write(f"**Q{q['qnum']}**. {q['question']}")
                opts = q['options']
                labels = ['A','B','C','D']
                choices = []
                if any(o.strip() for o in opts):
                    for i,o in enumerate(opts):
                        if o and o.strip():
                            choices.append(f"{labels[i]}. {o}")
                    sel = st.radio(f"Q{q['qnum']}", choices, key=f"q_{q['qnum']}")
                    st.session_state['user_answers'][q['qnum']] = sel.split('.')[0].strip()
                else:
                    txt = st.text_area(f"Q{q['qnum']} answer (no options detected)", key=f"free_{q['qnum']}")
                    st.session_state['user_answers'][q['qnum']] = txt
            submit_btn = st.form_submit_button("Submit Exam")

    if 'submit_btn' in locals() and submit_btn:
        # check time
        if st.session_state.get('end_time') and time.time() > st.session_state['end_time']:
            st.warning("Time exceeded — submission recorded but time over.")
        total, corr, inc, details = evaluate_responses(questions, st.session_state['user_answers'], st.session_state['answer_key'], negative_mark=negative_mark, marks_per_q=marks_per_q)
        st.success(f"Score: {total} | Correct: {corr} | Incorrect: {inc}")
        with st.expander("Per-question details"):
            for d in details:
                st.write(f"Q{d['qnum']}: your={d['user']} correct={d['correct']} -> {'Correct' if d['is_correct'] else 'Wrong'}")
        buf = BytesIO()
        generate_result_pdf(student_name or "Student", exam_title, details, total, buf)
        st.download_button("Download Result PDF", data=buf, file_name=f"{exam_title.replace(' ','_')}_result.pdf", mime='application/pdf')

else:
    st.info("Upload Question PDF (E+H) to begin. After parsing, edit questions if needed, upload solution PDF, then Start Exam.")

st.markdown("---")
st.caption("Notes: The parser is tuned for two-column English pages that strictly alternate with Hindi pages. If parsing misses some questions or option lines, edit them in the parsed-question editor before starting the exam.")
