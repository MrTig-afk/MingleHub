"""CI guard: fail if dangerous code-exec patterns appear in the codebase."""
import os
import re


def test_no_dangerous_code_exec_sinks():
    """Grep api/ and scripts/ for eval/exec/pickle/os.system/subprocess
    and f-string SQL. Fail if any are found."""
    dangerous_patterns = [
        (r'\beval\s*\(', 'eval()'),
        (r'\bexec\s*\(', 'exec()'),
        (r'\bpickle\.loads?\s*\(', 'pickle.load/loads'),
        (r'\bos\.system\s*\(', 'os.system()'),
        (r'\bsubprocess\.', 'subprocess'),
    ]
    # f-string SQL: f-string with interpolation that also contains a SQL keyword as a
    # standalone SQL token (preceded by whitespace or string start, not a URL path slash/dash).
    # This avoids false positives from URL paths like f"/sessions/{id}/select-hot-seat".
    sql_keywords = ['SELECT', 'INSERT', 'UPDATE', 'DELETE', 'ALTER', 'DROP']
    fstring_sql_pattern = re.compile(
        r'''f['"].*\{.*\}.*(?<![/-])(?<![a-z])(?:''' + '|'.join(sql_keywords) + r''')(?![a-z-])''',
        re.IGNORECASE,
    )

    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    scan_dirs = [os.path.join(root, 'api'), os.path.join(root, 'scripts')]
    this_file = os.path.abspath(__file__)
    violations = []

    for scan_dir in scan_dirs:
        for dirpath, _, filenames in os.walk(scan_dir):
            for fname in filenames:
                if not fname.endswith('.py'):
                    continue
                fpath = os.path.join(dirpath, fname)
                if os.path.abspath(fpath) == this_file:
                    continue  # Don't scan ourselves
                with open(fpath, 'r', encoding='utf-8', errors='ignore') as f:
                    for lineno, line in enumerate(f, 1):
                        for pattern, label in dangerous_patterns:
                            if re.search(pattern, line):
                                violations.append(f"{fpath}:{lineno} -- {label}: {line.strip()}")
                        if fstring_sql_pattern.search(line):
                            violations.append(f"{fpath}:{lineno} -- f-string SQL: {line.strip()}")

    assert not violations, (
        "Dangerous code-exec patterns found:\n" + "\n".join(violations)
    )
