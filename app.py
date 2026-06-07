import difflib
import html
import io
import json
import re
import time
import tokenize
from dataclasses import dataclass

import streamlit as st
import streamlit.components.v1 as components


st.set_page_config(
    page_title="Code Theo Mau",
    page_icon="</>",
    layout="wide",
    initial_sidebar_state="expanded",
)


DEFAULT_SAMPLE = """def fibonacci(n):
    if n <= 1:
        return n
    return fibonacci(n - 1) + fibonacci(n - 2)


for i in range(8):
    print(fibonacci(i))
"""


@dataclass
class CompareResult:
    percent: float
    issues: int
    matched_chars: int
    expected_chars: int
    actual_chars: int


def init_state() -> None:
    defaults = {
        "sample_code": DEFAULT_SAMPLE,
        "sample_cells": [DEFAULT_SAMPLE],
        "selected_cell_index": 0,
        "practice_code": "",
        "timer_running": False,
        "timer_started_at": None,
        "elapsed_before_start": 0.0,
    }
    for key, value in defaults.items():
        st.session_state.setdefault(key, value)


def extract_uploaded_cells(uploaded_file) -> list[str]:
    raw = uploaded_file.read()
    name = uploaded_file.name.lower()

    if name.endswith(".ipynb"):
        notebook = json.loads(raw.decode("utf-8"))
        code_cells = []
        for cell in notebook.get("cells", []):
            if cell.get("cell_type") != "code":
                continue

            source = cell.get("source", "")
            text = clean_notebook_text(source_to_text(source))
            if text.strip():
                code_cells.append(text)

        return code_cells or [""]

    return [raw.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")]


def source_to_text(source) -> str:
    if isinstance(source, str):
        return source

    if isinstance(source, list):
        return "".join(source_to_text(item) for item in source)

    if isinstance(source, dict):
        for key in ("source", "text", "code", "value"):
            if key in source:
                return source_to_text(source[key])
        return ""

    return "" if source is None else str(source)


def clean_notebook_text(text: str) -> str:
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    cleaned = []
    for line in lines:
        line = re.sub(r",?\s*\[object\s+object\]\s*,?", "", line, flags=re.IGNORECASE)
        if line.strip():
            cleaned.append(line)
    return "\n".join(cleaned)


def sanitize_session_sample() -> None:
    sample_code = clean_notebook_text(st.session_state.sample_code)
    if sample_code != st.session_state.sample_code:
        st.session_state.sample_code = sample_code

    cells = st.session_state.get("sample_cells", [])
    cleaned_cells = [clean_notebook_text(cell) for cell in cells]
    cleaned_cells = [cell for cell in cleaned_cells if cell.strip()]
    if cleaned_cells and cleaned_cells != cells:
        st.session_state.sample_cells = cleaned_cells
        st.session_state.selected_cell_index = min(
            int(st.session_state.selected_cell_index),
            len(cleaned_cells) - 1,
        )


def normalize_code(code: str, ignore_trailing_spaces: bool, ignore_empty_lines: bool) -> str:
    code = code.replace("\r\n", "\n").replace("\r", "\n")
    lines = code.split("\n")

    if ignore_trailing_spaces:
        lines = [line.rstrip() for line in lines]

    if ignore_empty_lines:
        lines = [line for line in lines if line.strip()]

    return "\n".join(lines)


def remove_python_comments(code: str) -> str:
    try:
        tokens = tokenize.generate_tokens(io.StringIO(code).readline)
        kept_tokens = []
        for token in tokens:
            token_type, token_text, _, _, _ = token
            if token_type == tokenize.COMMENT:
                continue
            kept_tokens.append(token)
        uncommented = tokenize.untokenize(kept_tokens)
    except tokenize.TokenError:
        uncommented = "\n".join(
            line for line in code.split("\n") if not line.lstrip().startswith("#")
        )

    return "\n".join(
        line for line in uncommented.split("\n") if not line.lstrip().startswith("#")
    )


def compare_code(expected: str, actual: str) -> CompareResult:
    matcher = difflib.SequenceMatcher(None, expected, actual)
    matched = sum(block.size for block in matcher.get_matching_blocks())
    expected_chars = len(expected)
    actual_chars = len(actual)
    percent = 100.0 if expected_chars == actual_chars == 0 else matcher.ratio() * 100
    issues = sum(1 for tag, _, _, _, _ in matcher.get_opcodes() if tag != "equal")

    return CompareResult(percent, issues, matched, expected_chars, actual_chars)


def render_char_diff(expected_line: str, actual_line: str) -> str:
    matcher = difflib.SequenceMatcher(None, expected_line, actual_line)
    parts = []

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        actual_piece = actual_line[j1:j2]
        expected_piece = expected_line[i1:i2]

        if tag == "equal":
            parts.append(html.escape(actual_piece))
        elif tag == "insert":
            parts.append(f"<span class='extra'>{html.escape(actual_piece)}</span>")
        elif tag == "delete":
            missing = html.escape(expected_piece) or "&nbsp;"
            parts.append(f"<span class='missing'>{missing}</span>")
        elif tag == "replace":
            if actual_piece:
                parts.append(f"<span class='wrong'>{html.escape(actual_piece)}</span>")
            if len(expected_piece) > len(actual_piece):
                missing_tail = html.escape(expected_piece[len(actual_piece) :])
                parts.append(f"<span class='missing'>{missing_tail}</span>")

    return "".join(parts) if parts else "&nbsp;"


def render_diff_panel(expected: str, actual: str) -> str:
    expected_lines = expected.split("\n")
    actual_lines = actual.split("\n")
    rows = []
    total = max(len(expected_lines), len(actual_lines))

    for idx in range(total):
        expected_line = expected_lines[idx] if idx < len(expected_lines) else ""
        actual_line = actual_lines[idx] if idx < len(actual_lines) else ""
        is_ok = expected_line == actual_line
        status = "ok" if is_ok else "bad"
        marker = "OK" if is_ok else "!"
        rendered = html.escape(actual_line) if is_ok else render_char_diff(expected_line, actual_line)

        if not actual_line and expected_line:
            rendered = f"<span class='missing'>{html.escape(expected_line)}</span>"

        rows.append(
            "<div class='diff-row'>"
            f"<span class='line-no'>{idx + 1}</span>"
            f"<span class='line-status {status}'>{marker}</span>"
            f"<code>{rendered}</code>"
            "</div>"
        )

    return "\n".join(rows)


def current_elapsed() -> float:
    elapsed = float(st.session_state.elapsed_before_start)
    if st.session_state.timer_running and st.session_state.timer_started_at is not None:
        elapsed += time.time() - float(st.session_state.timer_started_at)
    return elapsed


def format_elapsed(seconds: float) -> str:
    seconds = int(seconds)
    minutes, sec = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours:02d}:{minutes:02d}:{sec:02d}"


def start_timer() -> None:
    if not st.session_state.timer_running:
        st.session_state.timer_running = True
        st.session_state.timer_started_at = time.time()


def pause_timer() -> None:
    if st.session_state.timer_running:
        st.session_state.elapsed_before_start = current_elapsed()
        st.session_state.timer_running = False
        st.session_state.timer_started_at = None


def reset_timer() -> None:
    st.session_state.timer_running = False
    st.session_state.timer_started_at = None
    st.session_state.elapsed_before_start = 0.0


def set_sample_from_selected_cell() -> None:
    cells = st.session_state.get("sample_cells", [DEFAULT_SAMPLE])
    index = int(st.session_state.get("selected_cell_index", 0))
    index = max(0, min(index, len(cells) - 1))
    st.session_state.selected_cell_index = index
    st.session_state.sample_code = cells[index]
    st.session_state.practice_code = ""
    reset_timer()


def load_sample_from_upload() -> None:
    uploaded = st.session_state.get("uploaded_sample")
    if uploaded is None:
        return

    st.session_state.sample_cells = extract_uploaded_cells(uploaded)
    st.session_state.selected_cell_index = 0
    st.session_state.sample_code = st.session_state.sample_cells[0]
    st.session_state.practice_code = ""
    reset_timer()


def app_css() -> None:
    st.markdown(
        """
        <style>
        .block-container {
            padding-top: 1.35rem;
        }

        [data-testid="stMetricValue"] {
            font-size: 1.35rem;
        }

        .code-box,
        .diff-panel {
            border: 1px solid #d8dee9;
            border-radius: 8px;
            background: #fbfcfe;
            overflow: auto;
        }

        .code-box {
            min-height: 320px;
            max-height: 48vh;
            resize: vertical;
        }

        .code-box pre {
            margin: 0;
            padding: 16px 18px;
            font-size: 14px;
            line-height: 1.55;
            white-space: pre;
            min-width: max-content;
        }

        .diff-panel {
            min-height: 220px;
            max-height: 38vh;
            resize: vertical;
            font-family: ui-monospace, SFMono-Regular, Consolas, "Liberation Mono", monospace;
            font-size: 13px;
            line-height: 1.55;
        }

        textarea {
            font-family: ui-monospace, SFMono-Regular, Consolas, "Liberation Mono", monospace !important;
            line-height: 1.55 !important;
            resize: vertical !important;
        }

        .diff-row {
            display: grid;
            grid-template-columns: 42px 28px minmax(0, 1fr);
            gap: 6px;
            padding: 1px 10px;
            border-bottom: 1px solid #eef1f5;
            align-items: start;
        }

        .diff-row code {
            white-space: pre-wrap;
            word-break: break-word;
            color: #172033;
            background: transparent;
            padding: 0;
        }

        .line-no {
            color: #7a869a;
            text-align: right;
            user-select: none;
        }

        .line-status {
            min-width: 22px;
            height: 18px;
            border-radius: 999px;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            font-size: 10px;
            font-weight: 700;
            margin-top: 1px;
            padding: 0 4px;
        }

        .line-status.ok {
            color: #137333;
            background: #dff3e6;
        }

        .line-status.bad {
            color: #b42318;
            background: #ffe4df;
        }

        .wrong,
        .extra,
        .missing {
            border-radius: 4px;
            padding: 0 2px;
        }

        .wrong {
            color: #b42318;
            background: #ffd7d2;
            box-shadow: inset 0 -2px 0 #d92d20;
        }

        .extra {
            color: #9f580a;
            background: #ffefc6;
            box-shadow: inset 0 -2px 0 #dc6803;
        }

        .missing {
            color: #b42318;
            background: #ffe4df;
            border: 1px dashed #d92d20;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def main() -> None:
    init_state()
    sanitize_session_sample()
    app_css()

    st.title("Luyen code theo mau")

    with st.sidebar:
        st.subheader("Mau code")
        st.file_uploader(
            "Tai file .py hoac .ipynb",
            type=["py", "ipynb"],
            key="uploaded_sample",
            on_change=load_sample_from_upload,
        )

        sample_cells = st.session_state.get("sample_cells", [st.session_state.sample_code])
        if len(sample_cells) > 1:
            st.selectbox(
                "Chon code cell",
                options=list(range(len(sample_cells))),
                format_func=lambda idx: f"Cell {idx + 1} - {len(sample_cells[idx].splitlines())} dong",
                key="selected_cell_index",
                on_change=set_sample_from_selected_cell,
            )

        st.text_area(
            "Hoac sua/dan mau vao day",
            key="sample_code",
            height=220,
        )

        st.divider()
        st.subheader("So sanh")
        ignore_trailing_spaces = st.checkbox("Bo qua khoang trang cuoi dong", value=True)
        ignore_empty_lines = st.checkbox("Bo qua dong trong", value=False)
        show_autorefresh = st.checkbox("Cap nhat dong ho lien tuc", value=False)

        st.divider()
        st.subheader("Dong ho")
        st.metric("Thoi gian", format_elapsed(current_elapsed()))
        timer_cols = st.columns(3)
        if timer_cols[0].button("Bat dau", use_container_width=True):
            start_timer()
            st.rerun()
        if timer_cols[1].button("Dung", use_container_width=True):
            pause_timer()
            st.rerun()
        if timer_cols[2].button("Dat lai", use_container_width=True):
            reset_timer()
            st.rerun()

        if show_autorefresh and st.session_state.timer_running:
            components.html("<script>setTimeout(() => parent.location.reload(), 1000)</script>", height=0)

    expected = normalize_code(
        remove_python_comments(st.session_state.sample_code),
        ignore_trailing_spaces=ignore_trailing_spaces,
        ignore_empty_lines=ignore_empty_lines,
    )
    actual = normalize_code(
        remove_python_comments(st.session_state.practice_code),
        ignore_trailing_spaces=ignore_trailing_spaces,
        ignore_empty_lines=ignore_empty_lines,
    )
    result = compare_code(expected, actual)

    top_cols = st.columns(4)
    top_cols[0].metric("Do giong", f"{result.percent:.1f}%")
    top_cols[1].metric("Cum loi", result.issues)
    top_cols[2].metric("Ky tu mau", result.expected_chars)
    top_cols[3].metric("Ky tu da go", result.actual_chars)

    cell_count = len(st.session_state.get("sample_cells", []))
    if cell_count > 1:
        st.subheader(f"Mau can go - Cell {st.session_state.selected_cell_index + 1}/{cell_count}")
    else:
        st.subheader("Mau can go")
    st.markdown(
        "<div class='code-box'><pre><code>"
        + html.escape(st.session_state.sample_code)
        + "</code></pre></div>",
        unsafe_allow_html=True,
    )

    st.subheader("Bai go cua ban")
    st.text_area(
        "Nhap code",
        key="practice_code",
        height=520,
        label_visibility="collapsed",
        placeholder="Go lai code mau tai day...",
    )

    st.subheader("Kiem tra loi")
    st.markdown(
        "<div class='diff-panel'>"
        + render_diff_panel(expected, actual)
        + "</div>",
        unsafe_allow_html=True,
    )

    if expected == actual:
        st.success("Chinh xac. Ban da go khop mau hien tai.")
    else:
        st.info("Mau do: sai hoac thieu. Mau vang: ky tu thua. Cac dong chu thich khong tinh diem.")


if __name__ == "__main__":
    main()
