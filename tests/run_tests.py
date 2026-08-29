#!/usr/bin/env python3
"""
Test suite for pptext.

pptext has no Go test files and is not built as a package, so this suite drives
the compiled binary end-to-end: each case runs pptext over a fixture in
tests/fixtures/ and asserts against the generated report.html.

The report marks every check section with a colour that says whether that check
found anything:

    <span class='dim'>-----   check found nothing
    <span class='black'>----- check reported findings

That gives each case two-sided leverage. A case names the sections it expects to
be flagged; every other section in the report must come back clean. So a fixture
that targets one check also proves the other seventeen do not fire on it.

Run with:  make test        (or: python3 tests/run_tests.py)
Options:   -v  show per-assertion detail
           -l  list cases without running them
           -k  run only cases whose name contains the given substring
"""

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
FIXTURES = os.path.join(HERE, "fixtures")
BINARY = os.path.join(ROOT, "pptext")

# ---------------------------------------------------------------------------
# report parsing
# ---------------------------------------------------------------------------

TAG_RE = re.compile(r"<[^>]*>")
SPAN_RE = re.compile(r"<span class='(\w+)'>")
SECTION_RE = re.compile(r"^-{5}\s+(.*?)\s*-*$")
BANNER_RE = re.compile(r"^\*{80}$")
TITLE_RE = re.compile(r"^\*\s+(.*?)\s*\*$")


class Report:
    """A parsed report.html: check sections and top-level report blocks."""

    def __init__(self, raw):
        self.raw = raw
        self.sections = {}   # "scanno check"  -> {"status": "dim"|"black", "body": str}
        self.reports = {}    # "JEEBIES REPORT" -> {"status": ...,          "body": str}
        self.preamble = ""
        self._parse()

    def _parse(self):
        lines = raw_lines = self.raw.replace("\r\n", "\n").split("\n")
        plain = [TAG_RE.sub("", ln) for ln in raw_lines]

        def style_of(i):
            m = SPAN_RE.search(raw_lines[i])
            return m.group(1) if m else None

        # locate every section / top-level header, then slice bodies between them
        marks = []  # (index, kind, name, status)
        for i, text in enumerate(plain):
            m = SECTION_RE.match(text)
            if m and m.group(1):
                marks.append((i, "section", m.group(1), style_of(i)))
                continue
            if BANNER_RE.match(text) and i + 1 < len(plain):
                t = TITLE_RE.match(plain[i + 1])
                if t and t.group(1):
                    marks.append((i, "report", t.group(1), style_of(i)))

        for n, (i, kind, name, status) in enumerate(marks):
            end = marks[n + 1][0] if n + 1 < len(marks) else len(plain)
            body = "\n".join(plain[i + 1:end])
            target = self.sections if kind == "section" else self.reports
            # a repeated name (e.g. two banner lines) keeps the first, richer entry
            if name not in target:
                target[name] = {"status": status, "body": body}

        self.preamble = "\n".join(plain[:marks[0][0]]) if marks else "\n".join(plain)

    @property
    def flagged_sections(self):
        return {n for n, s in self.sections.items() if s["status"] == "black"}

    @property
    def text(self):
        return TAG_RE.sub("", self.raw)


# ---------------------------------------------------------------------------
# case definitions
# ---------------------------------------------------------------------------

CASES = []


def case(name, **kw):
    kw["name"] = name
    CASES.append(kw)


# --- the baseline -----------------------------------------------------------
# Every check must come back clean. This is the false-positive guard for the
# whole suite: if any check starts firing on ordinary well-formed prose, this
# case fails and points straight at it.
case(
    "clean-baseline",
    doc="well-formed text trips no check at all",
    fixture="clean-baseline.txt",
    flagged={},
    reports={"SPELLCHECK SUSPECT WORDS (en)": "dim",
             "EDIT DISTANCE CHECKS": "dim",
             "TEXT ANALYSIS REPORT": "dim",
             "JEEBIES REPORT": "dim"},
    contains=["Smart Quote Scan: no suspects reported",
              "punctuation style: American"],
)

# --- hyphenation ------------------------------------------------------------
case(
    "hyphenation-consistency",
    doc="motor-car vs motorcar; a lone hyphenated word is not reported",
    fixture="hyphenation-consistency.txt",
    flagged={"hyphenation and non-hyphenated check": [
        "motorcar (1) ❬-❭ motor-car (1)",
        "schoolhouse (1) ❬-❭ school-house (1)",
    ]},
    absent={"hyphenation and non-hyphenated check": ["sister-in-law"]},
)
case(
    "hyphen-space-consistency",
    doc="post-office vs post office; dining-room alone is not reported",
    fixture="hyphen-space-consistency.txt",
    flagged={"hyphenation and spaced pair check": ["'post-office' ❬-❭ 'post office'"]},
    absent={"hyphenation and spaced pair check": ["dining-room"]},
)

# --- whitespace and layout --------------------------------------------------
case(
    "asterisks",
    doc="asterisks are reported, including a thought-break line",
    fixture="asterisks.txt",
    # a thought break is spaced asterisks, so it also trips adjacent spaces,
    # and '*' is itself an infrequent character
    flagged={"asterisk checks": ["*       *       *", "The footnote marker *"],
             "adjacent spaces check": [],
             "character checks": ["'*'"]},
)
case(
    "adjacent-spaces",
    doc="interior double spaces reported; leading indent is not",
    fixture="adjacent-spaces.txt",
    flagged={"adjacent spaces check": ["He walked slowly  down", "The second offender  is here"]},
    absent={"adjacent spaces check": ["This line is indented"]},
)
case(
    "trailing-spaces",
    doc="trailing whitespace reported by both the dedicated check and gutcheck",
    fixture="trailing-spaces.txt",
    flagged={"trailing spaces check": ["ends with a single trailing space",
                                       "three trailing spaces"],
             # a paragraph ending in a space no longer ends in legal punctuation
             "special situations checks": ["trailing space on line"],
             "paragraph level checks": ["query: unexpected paragraph end"]},
    absent={"trailing spaces check": ["entirely free of any trailing"]},
)
case(
    "character-checks",
    doc="a rune used fewer than ten times is reported; common runes are not",
    fixture="character-checks.txt",
    flagged={"character checks": ["'§'"]},
    absent={"character checks": ["'a'", "'e'", "','"]},
)
case(
    "short-lines",
    doc="a short line followed by a non-blank line; indented lines are exempt",
    fixture="short-lines.txt",
    flagged={"short lines check": ["He stopped."]},
    absent={"short lines check": ["An indented line is exempt",
                                  "A short line at the end"]},
)
case(
    "long-lines",
    doc="lines over 72 runes, reported longest first with their length",
    fixture="long-lines.txt",
    flagged={"long lines check": ["(129)", "(113)"]},
    absent={"long lines check": ["A perfectly ordinary line"]},
)

# --- word and line level ----------------------------------------------------
case(
    "repeated-words",
    doc="a doubled word inside a paragraph",
    fixture="repeated-words.txt",
    flagged={"repeated word check": ["the the", "had had"]},
    absent={"repeated word check": ["No word is doubled"]},
)
case(
    "duplicate-lines",
    doc="a repeated two-line block is reported; a single repeated line is not",
    fixture="duplicate-lines.txt",
    flagged={"duplicate lines check": ["lines 3–4 are part of a duplicated-line cluster",
                                       "The night was cold"]},
    absent={"duplicate lines check": ["A single line may repeat once"]},
)
case(
    "ellipsis",
    doc="malformed ellipses; spaced and line-leading forms are allowed",
    fixture="ellipsis.txt",
    flagged={"ellipsis check": ["“Give... us", "“Give.. us", "“Give ..... us",
                                "trailing ellipsis like this ...",
                                ".The line above ended badly",
                                "But the cars. ..."],
             # gutcheck independently flags lines starting with punctuation
             "special situations checks": ["line starts with suspect punctuation"]},
    absent={"ellipsis check": ["A properly spaced ellipsis",
                               "... A line beginning with three dots",
                               "stopped...."]},
)
case(
    "dash-check",
    doc="stray dashes reported; legal hyphen, em-dash and en-dash uses are not",
    fixture="dash-check.txt",
    flagged={"dash check": ["adjacent dashes:", "He said --",
                            "hyphen-minus:", "a stand - alone hyphen"],
             "character checks": ["'–'"]},
    absent={"dash check": ["well-known", "He stopped—then", "1850–1860",
                           "Mr. ——", "before he went out—"]},
)

# --- footnotes --------------------------------------------------------------
# The footnote section goes black whenever footnotes exist at all -- it is a
# summary, not strictly an error report -- so all of these expect it flagged.
case(
    "footnotes-normal",
    doc="anchors and definitions numbered 1..3 form one unbroken series",
    fixture="footnotes-normal.txt",
    flagged={"footnote check": ["found footnote anchors: 1–3 (count: 3)",
                                "found footnotes: 1–3 (count: 3)"],
             # brackets are infrequent runes, and gutcheck reads "[1]" as a
             # standalone 1 -- both fire on any footnoted text
             "character checks": ["'['", "']'"],
             "special situations checks": ["standalone 1"]},
)
case(
    "footnotes-gap",
    doc="a gap in the sequence splits the numbering into two series",
    fixture="footnotes-gap.txt",
    flagged={"footnote check": ["found footnote anchors:", "1–2", "5",
                                "(total count: 3)"],
             "character checks": ["'['", "']'"],
             "special situations checks": ["standalone 1"]},
)
case(
    "footnotes-multiple-series",
    doc="numbering that restarts per chapter yields repeated series",
    fixture="footnotes-multiple-series.txt",
    flagged={"footnote check": ["found footnote anchors:", "found footnotes:",
                                "(total count: 4)"],
             "character checks": ["'['", "']'"],
             "special situations checks": ["standalone 1"]},
)
case(
    "footnotes-bracketed",
    doc="[Footnote n: form, both flush left and indented",
    fixture="footnotes-bracketed.txt",
    flagged={"footnote check": ["found footnote anchors: 1–2 (count: 2)",
                                "found footnotes: 1–2 (count: 2)"],
             # "[F" is not one of the bracket openers gutcheck allows
             "special situations checks": ["opening square bracket followed by "
                                           "other than I, G, M, S or number"],
             "character checks": ["'['", "']'"]},
)
case(
    "footnotes-initial-not-one",
    doc="a series that starts at 2 is reported as 2-3, not silently renumbered",
    fixture="footnotes-initial-not-one.txt",
    flagged={"footnote check": ["found footnote anchors: 2–3 (count: 2)",
                                "found footnotes: 2–3 (count: 2)"],
             "character checks": ["'['", "']'"]},
)

# --- scannos and quotes -----------------------------------------------------
case(
    "scanno-check",
    doc="words from scannos.txt are reported; their correct forms are not",
    fixture="scanno-check.txt",
    flagged={"scanno check": ["tho", "arid"]},
    absent={"scanno check": ["though", "“and”"]},
)
case(
    "scanno-goodwords",
    doc="-g exempts scannos, silencing the check entirely",
    fixture="scanno-check.txt",
    args=["-g", os.path.join(FIXTURES, "good-words.txt")],
    flagged={},
    contains=["good words file: good-words.txt", "good word count: 2 words"],
)
case(
    "curly-quotes",
    doc="floating quotes and wrong-facing quotes",
    fixture="curly-quotes.txt",
    flagged={"curly quote check": ["floating quote", "quote direction",
                                   "The mark “ was left floating",
                                   "said.“That is quite enough",
                                   "The word before“the quote",
                                   "“Come in,”she said"],
             "special situations checks": ["quote error (context)"],
             "paragraph level checks": []},
    absent={"curly quote check": ["quoted correctly from beginning to end"]},
)

# --- book and paragraph level ----------------------------------------------
case(
    "book-level",
    doc="every book-level consistency check fires at once",
    fixture="book-level.txt",
    flagged={"book level checks": [
        "both straight and curly single quotes found in text",
        "both straight and curly double quotes found in text",
        'both "a.m." and "a. m." found in text',
        'both "today" and "to-day" found in text',
        "compass direction hyphenation inconsistency",
        'both "Mr." (1) and "Mr" (1) found in text',
        "both apostrophes and turned commas appear in text",
        "repeated line:",
    ],
        # deliberately mixing conventions lights up neighbouring checks too.
        # note "duplicate lines check" stays clean: a single line repeated
        # adjacently is book-level's business, not the duplicate-block check's.
        "paragraph level checks": ["full stop followed by unexpected sequence"],
        "character checks": ["'\\''", "'\"'"],
        "curly quote check": ["quote direction"],
        "hyphenation and non-hyphenated check": ["northeast (1) ❬-❭ north-east (1)"],
        "special situations checks": ["mixed case within word"]},
    # the ◨/◧ glyphs must reach the reader as real italic markup
    raw_contains=["<i>single</i>", "<i>double</i>", "<span class='black'>"],
)
case(
    "para-level",
    doc="every paragraph-level check fires at once",
    fixture="para-level.txt",
    flagged={"paragraph level checks": [
        "paragraph starts with upper-case word",
        "full stop followed by unexpected sequence",
        "query: missing paragraph break?",
        "incorrectly split paragraph",
        "query: he/be. (see also jeebies report)",
        "query: had/bad",
        "query: hut/but",
        "query: unexpected paragraph end",
    ],
        # the he/be, had/bad and hut/but paragraphs necessarily use words
        # that are themselves on the scanno list
        "scanno check": ["bad", "ball", "hut"]},
)

# --- gutcheck / special situations -----------------------------------------
case(
    "gutcheck-punctuation",
    doc="punctuation-shaped gutcheck reports",
    fixture="gutcheck-punctuation.txt",
    flagged={"special situations checks": [
        "punctuation after 'the'", "punctuation error", "comma spacing",
        "spaced punctuation", "I/! check", "disjointed contraction",
        "title abbreviation comma", "broken hyphenation", "date format",
        "abbreviation &c without period", "unexpected period after word",
    ],
        "character checks": ["'&'"],
        "dash check": ["hyphen-minus:"],
        "paragraph level checks": ["full stop followed by unexpected sequence"]},
)
case(
    "gutcheck-characters",
    doc="stray-character and stray-markup gutcheck reports",
    fixture="gutcheck-characters.txt",
    flagged={"special situations checks": [
        "single character line", "abandoned HTML tag",
        "Blank Page placeholder found", "mixed hyphen/dash",
        "line that starts with hyphen and then non-hyphen",
        "line starts with suspect punctuation", "ampersand character",
        "opening square bracket followed by other than I, G, M, S or number",
    ],
        "character checks": ["'&'", "'/'"],
        "dash check": ["hyphen-minus:"],
        "ellipsis check": [".A line that starts with a full stop"],
        "hyphenation and non-hyphenated check": [],
        "paragraph level checks": ["incorrectly split paragraph"]},
)
case(
    "gutcheck-numbers",
    doc="standalone digits and letter/number mixes; ordinals are exempt",
    fixture="gutcheck-numbers.txt",
    flagged={"special situations checks": [
        "standalone 0", "standalone 1", "mixed letters and numbers in word",
    ],
        "character checks": ["'$'"],
        # "1-5" escapes the standalone-1 rule but is still a bare hyphen-minus
        "dash check": ["The pages 1-5"]},
    absent={"special situations checks": [
        "1st and 2nd", "He paid $1", "pages 1-5", "1. Preface",
    ]},
)
case(
    "gutcheck-invisibles",
    doc="invisible characters and word-shape queries",
    fixture="gutcheck-invisibles.txt",
    flagged={"special situations checks": [
        "non-breaking space", "soft hyphen", "tab character",
        "unexpected comma after word", "mixed case within word",
    ],
        "character checks": ["'\\t'", "'\\u00a0'", "'\\u00ad'"]},
    absent={"special situations checks": ["A perfectly ordinary line"]},
)

# --- spellcheck, edit distance, jeebies ------------------------------------
case(
    "spellcheck",
    doc="aspell suspects are listed; correctly spelled words are not",
    fixture="spellcheck.txt",
    needs_aspell=True,
    reports={"SPELLCHECK SUSPECT WORDS (en)": "black"},
    report_contains={"SPELLCHECK SUSPECT WORDS (en)": ["recieve", "seperate", "Trelawney"]},
    report_absent={"SPELLCHECK SUSPECT WORDS (en)": ["ordinary", "correctly"]},
)
case(
    "spellcheck-goodwords",
    doc="-g removes a proper noun from the suspect list but keeps real errors",
    fixture="spellcheck.txt",
    args=["-g", os.path.join(FIXTURES, "spellcheck-goodwords.txt")],
    needs_aspell=True,
    report_contains={"SPELLCHECK SUSPECT WORDS (en)": ["recieve", "seperate"]},
    report_absent={"SPELLCHECK SUSPECT WORDS (en)": ["Trelawney"]},
)
case(
    "spellcheck-missing-goodwords-file",
    doc="a good words path that does not exist is reported, not fatal",
    fixture="spellcheck.txt",
    args=["-g", os.path.join(FIXTURES, "does-not-exist.txt")],
    needs_aspell=True,
    contains=["no " + os.path.join(FIXTURES, "does-not-exist.txt") + " found"],
    reports={"SPELLCHECK SUSPECT WORDS (en)": "black"},
)
case(
    "edit-distance",
    doc="an OCR-damaged word one edit from a good word in the same text",
    fixture="edit-distance.txt",
    needs_aspell=True,
    reports={"EDIT DISTANCE CHECKS": "black"},
    report_contains={"EDIT DISTANCE CHECKS": ["harbonr(1):harbour(2)"]},
)
case(
    "jeebies",
    doc="he/be confusion in both directions; correct usage is left alone",
    fixture="jeebies.txt",
    reports={"JEEBIES REPORT": "black"},
    report_contains={"JEEBIES REPORT": ["and be said", "must he taken"]},
    report_absent={"JEEBIES REPORT": ["would be glad"]},
)

# --- input handling ---------------------------------------------------------
case(
    "smartquote-unbalanced",
    doc="an unclosed double quote writes an annotated scanreport.txt",
    fixture="smartquote-unbalanced.txt",
    contains=["Smart Quote Scan: report generated in scanreport.txt"],
    files=["scanreport.txt"],
    scanreport_contains=["SMART QUOTE CHECKS (overlay format)", "[@NESK "],
)
case(
    "british-punctuation",
    doc="British quoting is detected and the smart quote scan is skipped",
    fixture="british-punctuation.txt",
    contains=["punctuation style: British",
              "Smart Quote Checks skipped (British-style punctuation)"],
    lacks=["SMART QUOTE SCAN"],
    no_files=["scanreport.txt"],
)
case(
    "bom-crlf",
    doc="a UTF-8 BOM and CRLF endings are stripped, leaving the text clean",
    fixture="bom-crlf.txt",
    flagged={},
    contains=["with BOM", "CRLF line terminators"],
    # if the BOM survived into the text it would show up as a rare character
    absent={"character checks": ["﻿"]},
)
case(
    "missing-input-file",
    doc="a nonexistent -i path is reported in the report rather than crashing",
    fixture="does-not-exist.txt",
    allow_missing_fixture=True,
    contains=["error opening file"],
)

# --- command line -----------------------------------------------------------
case(
    "flag-revision",
    doc="-r prints the build stamp and exits without producing a report",
    no_input=True,
    args=["-r"],
    stdout_contains=["Version:", "Built:"],
    no_files=["report.html"],
)
case(
    "flag-no-input",
    doc="omitting -i is a fatal error",
    no_input=True,
    args=[],
    exit_code=1,
    stderr_contains=["No input file specified"],
)
case(
    "select-smartquote-only",
    doc="-t q runs the smart quote scan alone",
    fixture="clean-baseline.txt",
    args=["-t", "q"],
    has_reports=["SMART QUOTE SCAN"],
    lacks_reports=["SPELLCHECK SUSPECT WORDS (en)", "EDIT DISTANCE CHECKS",
                   "TEXT ANALYSIS REPORT", "JEEBIES REPORT"],
)
case(
    "select-spellcheck-only",
    doc="-t s runs the spellcheck alone",
    fixture="spellcheck.txt",
    args=["-t", "s"],
    needs_aspell=True,
    has_reports=["SPELLCHECK SUSPECT WORDS (en)"],
    lacks_reports=["EDIT DISTANCE CHECKS", "TEXT ANALYSIS REPORT", "JEEBIES REPORT"],
)
case(
    "select-edit-distance-only",
    doc="-t e reports edit distance only, but still runs spellcheck to feed it",
    fixture="edit-distance.txt",
    args=["-t", "e"],
    needs_aspell=True,
    has_reports=["EDIT DISTANCE CHECKS"],
    lacks_reports=["SPELLCHECK SUSPECT WORDS (en)"],
    report_contains={"EDIT DISTANCE CHECKS": ["harbonr(1):harbour(2)"]},
)
case(
    "select-text-checks-only",
    doc="-t t runs the text checks without the two hyphenation subtests",
    fixture="hyphenation-consistency.txt",
    args=["-t", "t"],
    has_reports=["TEXT ANALYSIS REPORT"],
    lacks_reports=["SPELLCHECK SUSPECT WORDS (en)", "JEEBIES REPORT"],
    has_sections=["scanno check", "dash check"],
    lacks_sections=["hyphenation and non-hyphenated check",
                    "hyphenation and spaced pair check"],
)
case(
    "select-hyphenation-subtest-1",
    doc="-t 1 adds the hyphenation/non-hyphenated subtest only",
    fixture="hyphenation-consistency.txt",
    args=["-t", "1"],
    has_sections=["hyphenation and non-hyphenated check"],
    lacks_sections=["hyphenation and spaced pair check"],
    flagged={"hyphenation and non-hyphenated check": ["motorcar (1) ❬-❭ motor-car (1)"]},
)
case(
    "select-hyphenation-subtest-2",
    doc="-t 2 adds the hyphenation/spaced-pair subtest only",
    fixture="hyphen-space-consistency.txt",
    args=["-t", "2"],
    has_sections=["hyphenation and spaced pair check"],
    lacks_sections=["hyphenation and non-hyphenated check"],
    flagged={"hyphenation and spaced pair check": ["'post-office' ❬-❭ 'post office'"]},
)
case(
    "output-directory",
    doc="-o places report.html and runlog.txt in the chosen directory",
    fixture="clean-baseline.txt",
    files=["report.html", "runlog.txt"],
    runlog_contains=["command line:"],
)


# ---------------------------------------------------------------------------
# runner
# ---------------------------------------------------------------------------

class Failure(Exception):
    pass


def find_aspell():
    """pptext defaults to /usr/bin/aspell; find it wherever it actually lives."""
    if os.path.exists("/usr/bin/aspell"):
        return "/usr/bin/aspell"
    return shutil.which("aspell")


def needs_aspell(c):
    """True if this case will make pptext shell out to aspell.

    pptext calls aspell for test selections containing 'a', 's' or 'e', and
    also for 'q': the smart quote scan reaches aspell through its asqual()
    helper (pptext.go:653) even though main() gates it separately. A missing
    aspell binary is fatal in all of those, so the selection -- not a
    hand-applied flag -- decides whether a case can run without aspell.
    """
    if c.get("needs_aspell"):
        return True
    if c.get("no_input"):
        return False
    args = c.get("args", [])
    selection = "a"
    if "-t" in args:
        selection = args[args.index("-t") + 1]
    return any(ch in selection for ch in "aseq")


def check_contains(where, haystack, needles, checks):
    for needle in needles:
        ok = needle in haystack
        checks.append((ok, "%s contains %r" % (where, needle)))


def check_absent(where, haystack, needles, checks):
    for needle in needles:
        ok = needle not in haystack
        checks.append((ok, "%s omits %r" % (where, needle)))


def run_case(c, aspell, outdir):
    """Run one case; return a list of (passed, description) assertions."""
    checks = []
    args = [BINARY]

    if not c.get("no_input"):
        fixture = os.path.join(FIXTURES, c["fixture"])
        if not c.get("allow_missing_fixture") and not os.path.exists(fixture):
            raise Failure("fixture not found: %s" % fixture)
        args += ["-i", fixture, "-o", outdir]
    if aspell:
        args += ["-A", aspell]
    args += c.get("args", [])

    proc = subprocess.run(args, cwd=ROOT, capture_output=True, text=True)

    expected_exit = c.get("exit_code", 0)
    checks.append((proc.returncode == expected_exit,
                   "exit code is %d (got %d)" % (expected_exit, proc.returncode)))
    check_contains("stdout", proc.stdout, c.get("stdout_contains", []), checks)
    check_contains("stderr", proc.stderr, c.get("stderr_contains", []), checks)

    for fname in c.get("files", []):
        checks.append((os.path.exists(os.path.join(outdir, fname)),
                       "wrote %s" % fname))
    for fname in c.get("no_files", []):
        checks.append((not os.path.exists(os.path.join(outdir, fname)),
                       "did not write %s" % fname))

    for fname, key in (("scanreport.txt", "scanreport_contains"),
                       ("runlog.txt", "runlog_contains")):
        needles = c.get(key, [])
        if not needles:
            continue
        path = os.path.join(outdir, fname)
        if not os.path.exists(path):
            checks.append((False, "%s exists to be checked" % fname))
            continue
        with open(path, encoding="utf-8") as fh:
            check_contains(fname, fh.read(), needles, checks)

    report_path = os.path.join(outdir, "report.html")
    needs_report = any(k in c for k in (
        "flagged", "absent", "contains", "lacks", "reports", "report_contains",
        "report_absent", "has_sections", "lacks_sections", "has_reports",
        "lacks_reports", "raw_contains"))
    if not needs_report:
        return checks
    if not os.path.exists(report_path):
        checks.append((False, "report.html was produced"))
        return checks

    with open(report_path, encoding="utf-8") as fh:
        report = Report(fh.read())

    checks.append((report.raw.lstrip().startswith("<html>")
                   and report.raw.rstrip().endswith("</html>"),
                   "report.html is a complete HTML document"))

    # every section the case expects flagged is flagged, and nothing else is
    if "flagged" in c:
        expected = set(c["flagged"])
        actual = report.flagged_sections
        for name, needles in c["flagged"].items():
            present = name in report.sections
            checks.append((present, "section %r is present" % name))
            if not present:
                continue
            checks.append((report.sections[name]["status"] == "black",
                           "section %r reports findings" % name))
            check_contains("section %r" % name, report.sections[name]["body"],
                           needles, checks)
        for name in sorted(actual - expected):
            checks.append((False, "section %r unexpectedly reports findings" % name))
        # "spacing pattern" and "main text headers" are informational: they
        # carry no dim/black status, so they are neither flagged nor clean
        styled = {n for n, sec in report.sections.items() if sec["status"]}
        clean = sorted(styled - expected)
        if clean:
            checks.append((True, "%d other section(s) clean: %s"
                           % (len(clean), ", ".join(clean))))

    for name, needles in c.get("absent", {}).items():
        if name not in report.sections:
            checks.append((False, "section %r is present" % name))
            continue
        check_absent("section %r" % name, report.sections[name]["body"], needles, checks)

    for name in c.get("has_sections", []):
        checks.append((name in report.sections, "section %r is present" % name))
    for name in c.get("lacks_sections", []):
        checks.append((name not in report.sections, "section %r is absent" % name))
    for name in c.get("has_reports", []):
        checks.append((name in report.reports, "report %r is present" % name))
    for name in c.get("lacks_reports", []):
        checks.append((name not in report.reports, "report %r is absent" % name))

    for name, status in c.get("reports", {}).items():
        if name not in report.reports:
            checks.append((False, "report %r is present" % name))
            continue
        actual = report.reports[name]["status"]
        checks.append((actual == status,
                       "report %r is %s (got %s)" % (name, status, actual)))

    for name, needles in c.get("report_contains", {}).items():
        if name not in report.reports:
            checks.append((False, "report %r is present" % name))
            continue
        check_contains("report %r" % name, report.reports[name]["body"], needles, checks)
    for name, needles in c.get("report_absent", {}).items():
        if name not in report.reports:
            checks.append((False, "report %r is present" % name))
            continue
        check_absent("report %r" % name, report.reports[name]["body"], needles, checks)

    check_contains("report", report.text, c.get("contains", []), checks)
    check_absent("report", report.text, c.get("lacks", []), checks)
    check_contains("report HTML", report.raw, c.get("raw_contains", []), checks)
    return checks


def main():
    ap = argparse.ArgumentParser(description="pptext test suite")
    ap.add_argument("-v", "--verbose", action="store_true",
                    help="show every assertion, not just failures")
    ap.add_argument("-l", "--list", action="store_true", help="list cases and exit")
    ap.add_argument("-k", metavar="SUBSTR", help="run only cases matching SUBSTR")
    opts = ap.parse_args()

    cases = CASES
    if opts.k:
        cases = [c for c in cases if opts.k in c["name"]]

    if opts.list:
        for c in cases:
            print("%-34s %s" % (c["name"], c.get("doc", "")))
        return 0

    if not os.path.exists(BINARY):
        print("error: %s not found -- run 'make' first" % BINARY, file=sys.stderr)
        return 2
    for data in ("scannos.txt", "hebelist.txt"):
        if not os.path.exists(os.path.join(ROOT, data)):
            print("error: %s must sit beside the binary" % data, file=sys.stderr)
            return 2

    aspell = find_aspell()
    if not aspell:
        print("warning: aspell not found; spellcheck cases will be skipped\n")

    passed = failed = skipped = 0
    failures = []

    for c in cases:
        name = c["name"]
        if not aspell and needs_aspell(c):
            print("SKIP  %-34s (aspell not installed)" % name)
            skipped += 1
            continue
        with tempfile.TemporaryDirectory(prefix="pptext-test-") as outdir:
            try:
                checks = run_case(c, aspell, outdir)
            except Failure as exc:
                checks = [(False, str(exc))]
        bad = [d for ok, d in checks if not ok]
        if bad:
            failed += 1
            failures.append((name, bad))
            print("FAIL  %-34s %s" % (name, c.get("doc", "")))
        else:
            passed += 1
            print("ok    %-34s %s" % (name, c.get("doc", "")))
        if opts.verbose:
            for ok, desc in checks:
                print("        %s %s" % ("+" if ok else "-", desc))

    if failures:
        print("\n%s\nFAILURES\n%s" % ("=" * 70, "=" * 70))
        for name, bad in failures:
            print("\n%s:" % name)
            for desc in bad:
                print("  - %s" % desc)

    total = passed + failed
    print("\n%d/%d cases passed%s" % (passed, total,
                                      ", %d skipped" % skipped if skipped else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
