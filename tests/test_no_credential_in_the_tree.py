"""No credential in the tree — security review 24 Nov 2026, SR-20 (row 2026-09-23k's going-forward guard; row 2026-11-24p).

**This repository is public: a commit to `main` is published at once.** The 23 Sep audit found no credential here, and
this test keeps it so. It reads every tracked text file at the checked-out commit and fails on anything shaped like a
provider credential. **It prints the rule, the path and the line number — never the matched text**, so a red run does
not republish what it found in the CI log.

Ported from the app repo's `api/tests/test_no_credential_in_the_tree.py` (pfolio-app PR #801): same rules, same
allow-list design. Standard library only: no `ib_insync`, no network.

    python3 -m pytest tests/test_no_credential_in_the_tree.py -q          (from the repo root)

**If it goes red, rotate first.** A pushed branch is already on GitHub; removing the line does not un-publish it.

What it does not do: read history, or recognise a credential with no provider shape (a bare password in prose).
Files over 2 MB and binary files are not read.

`ALLOWED` names each known line by path, rule and a hash of the exact line, with its reason. A real credential added to
the same file still fails; an entry whose line is gone fails too, so the list cannot rot. It is empty today.
"""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
MAX_BYTES = 2_000_000

RULES = {
    'aws-access-key': re.compile(rb'\b(?:AKIA|ASIA)[0-9A-Z]{16}\b'),
    'stripe-live': re.compile(rb'\b(?:sk|rk)_live_[0-9A-Za-z]{16,}'),
    'stripe-test': re.compile(rb'\b(?:sk|rk)_test_[0-9A-Za-z]{16,}'),
    'stripe-webhook': re.compile(rb'\bwhsec_[0-9A-Za-z]{16,}'),
    'github-token': re.compile(rb'\b(?:ghp|gho|ghu|ghs|ghr)_[0-9A-Za-z]{30,}|\bgithub_pat_[0-9A-Za-z_]{40,}'),
    'slack-token': re.compile(rb'\bxox[baprs]-[0-9A-Za-z-]{10,}'),
    'sendgrid': re.compile(rb'\bSG\.[\w-]{20,24}\.[\w-]{40,46}'),
    'google-api-key': re.compile(rb'\bAIza[0-9A-Za-z_-]{35}'),
    'anthropic': re.compile(rb'\bsk-ant-[0-9A-Za-z_-]{20,}'),
    'openai-like': re.compile(rb'\bsk-(?:proj-)?[0-9A-Za-z]{32,}'),
    'private-key': re.compile(rb'-----BEGIN [A-Z ]*PRIVATE KEY-----'),
    'jwt': re.compile(rb'\beyJ[0-9A-Za-z_-]{10,}\.eyJ[0-9A-Za-z_-]{10,}\.[0-9A-Za-z_-]{10,}'),
    'service-account': re.compile(rb'"type"\s*:\s*"service_account"'),
}
#: A connection string with a password in it. Reported unless the password is an obvious placeholder.
CONNECTION = re.compile(
    rb'\b(?:postgres(?:ql)?(?:\+\w+)?|redis|rediss|mysql|mongodb(?:\+srv)?)://[^:/\s@\'"]+:(?P<password>[^@\s\'"]+)@'
)
PLACEHOLDER = re.compile(
    rb'^(?:\$\{?\w+\}?|<[^>]+>|\*+|x+|pfolio|password|pass|postgres|test|secret|user|changeme|example|pw|p|\.\.\.'
    rb'|\{[^}]*\}|%s)$',
    re.IGNORECASE,
)

#: Literal text each rule's match must contain. A rule's regex runs only on files holding one of its anchors: a
#: pattern opening on `\b` loses the regex engine's literal search, and 300 MB at 2-4 s a rule was 37 s; with the
#: anchors, a few seconds. `test_each_rule_catches_its_own_shape_and_only_that` goes through `scan`, so an anchor that
#: stopped matching its rule fails there.
ANCHORS = {
    'aws-access-key': (b'AKIA', b'ASIA'),
    'stripe-live': (b'_live_',),
    'stripe-test': (b'_test_',),
    'stripe-webhook': (b'whsec_',),
    'github-token': (b'ghp_', b'gho_', b'ghu_', b'ghs_', b'ghr_', b'github_pat_'),
    'slack-token': (b'xoxb-', b'xoxa-', b'xoxp-', b'xoxr-', b'xoxs-'),
    'sendgrid': (b'SG.',),
    'google-api-key': (b'AIza',),
    'anthropic': (b'sk-ant-',),
    'openai-like': (b'sk-',),
    'private-key': (b'PRIVATE KEY',),
    'jwt': (b'eyJ',),
    'service-account': (b'service_account',),
    'connection-string': (b'postgres', b'redis', b'mysql', b'mongodb'),
}

ALLOWED: dict[tuple[str, str, str], str] = {}


def _line_at(data: bytes, start: int) -> tuple[int, bytes]:
    begin = data.rfind(b'\n', 0, start) + 1
    end = data.find(b'\n', start)
    return data.count(b'\n', 0, start) + 1, data[begin : end if end != -1 else len(data)]


def _line_id(line: bytes) -> str:
    return hashlib.sha256(line.strip()).hexdigest()[:16]


def scan(data: bytes) -> list[tuple[str, int, str]]:
    """(rule, line number, line id) for every credential-shaped thing in `data` — never the text itself."""
    found = []
    for name, rx in RULES.items():
        if not any(a in data for a in ANCHORS[name]):
            continue
        for m in rx.finditer(data):
            no, line = _line_at(data, m.start())
            found.append((name, no, _line_id(line)))
    if not any(a in data for a in ANCHORS['connection-string']):
        return found
    for m in CONNECTION.finditer(data):
        if not PLACEHOLDER.match(m.group('password')):
            no, line = _line_at(data, m.start())
            found.append(('connection-string', no, _line_id(line)))
    return found


def _tracked_text():
    names = subprocess.run(['git', '-C', str(REPO), 'ls-files', '-z'], capture_output=True, check=True).stdout
    for raw in filter(None, names.split(b'\0')):
        rel = os.fsdecode(raw)
        path = REPO / rel
        if not path.is_file() or path.stat().st_size > MAX_BYTES:
            continue
        data = path.read_bytes()
        if b'\0' in data[:8000]:
            continue
        yield rel, data


def test_no_tracked_file_holds_a_credential():
    found, seen, read = [], set(), 0
    for rel, data in _tracked_text():
        read += 1
        for rule, no, line_id in scan(data):
            if (rel, rule, line_id) in ALLOWED:
                seen.add((rel, rule, line_id))
            else:
                found.append(f'{rule}  {rel}:{no}')
    assert read > 30, f'only {read} tracked text files read — is this a checkout?'
    assert found == [], (
        'credential-shaped text in tracked files (rule, path:line — the text is not printed). '
        'Rotate it first, then remove it:\n' + '\n'.join(sorted(found))
    )
    assert seen == set(ALLOWED), f'ALLOWED names lines that are gone: {sorted(set(ALLOWED) - seen)}'


#: One synthetic sample per rule, assembled at run time so this file never holds a match itself.
SAMPLES = {
    'aws-access-key': 'AKIA' + 'Z7' * 8,
    'stripe-live': 'sk_' + 'live_' + 'a1' * 12,
    'stripe-test': 'sk_' + 'test_' + 'a1' * 12,
    'stripe-webhook': 'whsec' + '_' + 'b2' * 12,
    'github-token': 'ghp' + '_' + 'c3' * 18,
    'slack-token': 'xox' + 'b-' + '1234567890-abc',
    'sendgrid': 'SG' + '.' + 'd4' * 11 + '.' + 'e5' * 22,
    'google-api-key': 'AIza' + 'f6' * 17 + 'g',
    'anthropic': 'sk-' + 'ant-' + 'h7' * 15,
    'openai-like': 'sk-' + 'proj-' + 'i8' * 20,
    'private-key': '-----BEGIN ' + 'RSA PRIVATE KEY-----',
    'jwt': 'eyJ' + 'j9' * 8 + '.eyJ' + 'k0' * 8 + '.' + 'l1' * 8,
    'service-account': '"type": ' + '"service_' + 'account"',
    'connection-string': 'postgresql://app:' + 'Zq9-long' + '-pass@db.example.com/x',
}


def test_each_rule_catches_its_own_shape_and_only_that():
    assert set(SAMPLES) == set(RULES) | {'connection-string'} == set(ANCHORS)
    for rule, sample in SAMPLES.items():
        data = f'line one\nconfig = "{sample}"\n'.encode()
        assert [(r, no) for r, no, _ in scan(data)] == [(rule, 2)], rule


def test_placeholders_and_local_examples_pass():
    for text in (
        'postgresql+psycopg://pfolio:pfolio@127.0.0.1:5432/pfolio',
        'postgresql://user:${DB_PASSWORD}@host/db',
        'redis://default:<password>@host:6379',
        'sk_live_…',
        'the key starts AKIA and is twenty characters',
    ):
        assert scan(text.encode()) == [], text
