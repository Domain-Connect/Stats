#!/usr/bin/env python3
"""
Domain Connect Templates - Syntax Rules Checker

Checks template JSON files against defined syntax rules.

Rules implemented:
  1. syncRedirectDomain: must match ABNF dc-host-list
       dc-host-list = domain-name *( *SP "," *SP domain-name )
  2. providerId, serviceId, groupId (per record): must match ABNF dc-id
       dc-id = 1*63( ALPHA / DIGIT / "-" / "_" / "." )
  3. syncPubKeyDomain: must be a valid dc-pubkey-domain
       dc-underscore-label = "_" 1*( ALPHA / DIGIT / "-" ) ; max 63 octets
       dc-pubkey-domain    = *( dc-underscore-label "." ) domain-name
  4. providerName, serviceName: must match ABNF dc-display-name
       dc-display-name = 1*255unicode-assignable
       unicode-assignable per RFC 9839 Section 4.3: all code points except
       surrogates (U+D800-U+DFFF), C0/C1 controls (except tab/LF/CR),
       and noncharacters (U+FDD0-U+FDEF, U+*FFFE, U+*FFFF)
  5. sharedProviderName, sharedServiceName, syncBlock, multiInstance,
     hostRequired, shared, warnPhishing: must be JSON boolean (true / false)
  6. description, variableDescription: must match ABNF dc-description-text
       dc-description-text = 0*2048unicode-assignable
  7. version: when present, must be a positive integer with no leading zeros
       dc-version = %x31-39 *DIGIT
                    ; positive integer, no leading zeros, no fraction, no exponent
  8. logoUrl: when present, must be a valid URI per RFC 3986 with scheme "https"

Usage:
    python scripts/check_syntax_rules.py [--folder FOLDER]

    Options:
        --folder FOLDER   Path to templates folder (default: 'Templates')
"""

import argparse
import json
import re
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# ABNF: domain-name  (RFC 5890, Section 2.3.2.3)
#
# An Internationalized Domain Name is a dot-separated sequence of labels
# where each label is one of:
#
#   NR-LDH label  (Section 2.3.2.2)
#     - ASCII letters, digits, hyphens only
#     - No leading or trailing hyphen
#     - 1–63 octets
#     - Must NOT start with "xn--" (that would be an A-label)
#
#   A-label  (Section 2.3.2.1)
#     - Starts with "xn--" (case-insensitive ACE prefix)
#     - Followed by 1–59 ASCII alphanumeric/hyphen characters (Punycode payload)
#     - Total label length 1–63 octets
#
#   U-label  (Section 2.3.2.1)
#     - Contains at least one non-ASCII Unicode character
#     - No leading or trailing hyphen
#     - 1–63 characters (Unicode)
#
# Total length (including dots) ≤ 253 octets (UTF-8 encoded).
# ---------------------------------------------------------------------------

# NR-LDH: ASCII alnum/hyphen, no leading/trailing hyphen, not starting with xn--
_NR_LDH_RE = re.compile(
    r'^(?!xn--)[a-zA-Z0-9](?:[a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?$'
)

# A-label: xn-- prefix + 1..59 LDH characters (Punycode payload)
_A_LABEL_RE = re.compile(
    r'^[xX][nN]--[a-zA-Z0-9\-]{1,59}$'
)


def _is_valid_label(label: str) -> bool:
    """Return True if label is a valid NR-LDH label, A-label, or U-label."""
    if not label or len(label) > 63:
        return False
    # U-label: contains at least one non-ASCII character
    if any(ord(ch) > 127 for ch in label):
        # Must not start or end with a hyphen
        return not (label.startswith('-') or label.endswith('-'))
    # A-label
    if label.lower().startswith('xn--'):
        return bool(_A_LABEL_RE.match(label))
    # NR-LDH label
    return bool(_NR_LDH_RE.match(label))


def is_valid_domain_name(value: str) -> bool:
    """Return True if value is a valid domain-name per RFC 5890 Section 2.3.2.3.

    Accepts single-label and multi-label names; total wire length ≤ 253 octets.
    """
    if not value or len(value.encode('utf-8')) > 253:
        return False
    return all(_is_valid_label(label) for label in value.split('.'))


def check_dc_host_list(value: str) -> list[str]:
    """
    Validate a dc-host-list value:
        dc-host-list = domain-name *( *SP "," *SP domain-name )

    *SP allows zero or more ASCII space characters (0x20) around commas.
    Returns a list of error strings (empty list means valid).
    """
    errors = []
    parts = value.split(',')
    for i, part in enumerate(parts):
        # Strip only ASCII spaces (SP = 0x20) as per ABNF *SP
        if not is_valid_domain_name(part.strip(' ')):
            errors.append(f"entry {i + 1} is not a valid domain-name: {part!r}")
    return errors


# ---------------------------------------------------------------------------
# ABNF: dc-pubkey-domain  (syncPubKeyDomain rule)
#
#   dc-underscore-label =  "_" 1*( ALPHA / DIGIT / "-" )
#                          ; RFC 8552 underscore-prefixed DNS label,
#                          ; max 63 octets total
#
#   dc-pubkey-domain    =  *( dc-underscore-label "." ) domain-name
#                          ; zero or more underscore labels prepended to
#                          ; an IDNA domain name
# ---------------------------------------------------------------------------
_UNDERSCORE_LABEL_RE = re.compile(r'^_[a-zA-Z0-9\-]{1,62}$')


def _is_valid_underscore_label(label: str) -> bool:
    """Return True if label is a valid dc-underscore-label."""
    return bool(_UNDERSCORE_LABEL_RE.match(label))


def is_valid_dc_pubkey_domain(value: str) -> bool:
    """Return True if value is a valid dc-pubkey-domain.

    Splits off any leading underscore labels (each followed by a dot),
    then validates the remainder as a domain-name per RFC 5890.
    """
    if not value or len(value.encode('utf-8')) > 253:
        return False
    labels = value.split('.')
    # Consume leading underscore labels; at least one non-underscore label
    # must remain to form the domain-name tail.
    i = 0
    while i < len(labels) and labels[i].startswith('_'):
        if not _is_valid_underscore_label(labels[i]):
            return False
        i += 1
    # The remaining labels must form a valid domain-name (at least one label).
    tail_labels = labels[i:]
    if not tail_labels:
        return False
    return all(_is_valid_label(label) for label in tail_labels)


# ---------------------------------------------------------------------------
# ABNF: dc-display-name  (RFC 9839, Section 4.3)
#
#   dc-display-name = 1*255unicode-assignable
#
# "Unicode Assignable" excludes:
#   - Surrogates:        U+D800–U+DFFF
#   - C0 controls:       U+0000–U+001F, except U+0009 (TAB), U+000A (LF),
#                        U+000D (CR)
#   - C1 controls:       U+007F–U+009F
#   - Noncharacters:     U+FDD0–U+FDEF
#                        U+*FFFE and U+*FFFF for all 17 Unicode planes
# ---------------------------------------------------------------------------

# Set of all noncharacter code points (U+*FFFE / U+*FFFF across 17 planes)
_NONCHARACTERS = frozenset(
    cp
    for plane in range(17)
    for cp in (0xFFFE + plane * 0x10000, 0xFFFF + plane * 0x10000)
) | frozenset(range(0xFDD0, 0xFDF0))


def _is_unicode_assignable(cp: int) -> bool:
    """Return True if code point cp is a Unicode Assignable per RFC 9839 Section 4.3."""
    # Surrogates
    if 0xD800 <= cp <= 0xDFFF:
        return False
    # C0 controls (except TAB U+0009, LF U+000A, CR U+000D)
    if 0x0000 <= cp <= 0x001F and cp not in (0x0009, 0x000A, 0x000D):
        return False
    # DEL + C1 controls
    if 0x007F <= cp <= 0x009F:
        return False
    # Noncharacters
    if cp in _NONCHARACTERS:
        return False
    return True


def is_valid_dc_display_name(value: str) -> bool:
    """Return True if value is a valid dc-display-name per RFC 9839 Section 4.3.

    Must be 1–255 Unicode Assignable code points.
    """
    if not value or len(value) > 255:
        return False
    return all(_is_unicode_assignable(ord(ch)) for ch in value)


def is_valid_dc_description_text(value: str) -> bool:
    """Return True if value is a valid dc-description-text per RFC 9839 Section 4.3.

    Must be 0–2048 Unicode Assignable code points (empty string is valid).
    """
    if len(value) > 2048:
        return False
    return all(_is_unicode_assignable(ord(ch)) for ch in value)


# ---------------------------------------------------------------------------
# URI validation  (RFC 3986)
#
# Uses the generic URI syntax regex from RFC 3986 Appendix B:
#   ^(([^:/?#]+):)(//([^/?#]*))?([^?#]*)(\?([^#]*))?(#(.*))?
# This decomposes any string into URI components without rejecting anything
# at the regex stage; scheme is then checked separately for "https".
# ---------------------------------------------------------------------------
_URI_RE = re.compile(
    r'^([^:/?#]+):'       # scheme ":"
    r'(//[^/?#]*)?'       # optional "//" authority
    r'[^?#]*'             # path
    r'(\?[^#]*)?'         # optional "?" query
    r'(#.*)?$'            # optional "#" fragment
)


def is_valid_https_uri(value: str) -> bool:
    """Return True if value is a valid RFC 3986 URI with scheme 'https'."""
    m = _URI_RE.match(value)
    if not m:
        return False
    scheme = m.group(1)
    return scheme.lower() == 'https'


# ---------------------------------------------------------------------------
# ABNF: dc-id
#   dc-id = 1*63( ALPHA / DIGIT / "-" / "_" / "." )
# ---------------------------------------------------------------------------
_DC_ID_RE = re.compile(r'^[a-zA-Z0-9\-_.]{1,63}$')


def is_valid_dc_id(value: str) -> bool:
    """Return True if value is a valid dc-id."""
    return bool(_DC_ID_RE.match(value))


# ---------------------------------------------------------------------------
# Rule registry
#   Each rule is a callable(template_data) -> list[str] of error messages.
# ---------------------------------------------------------------------------

def rule_sync_redirect_domain(data: dict) -> list[str]:
    """syncRedirectDomain must be a valid dc-host-list (spaces around commas allowed)."""
    value = data.get('syncRedirectDomain')
    if value is None:
        return []  # field is optional; absence is fine
    if not isinstance(value, str):
        return [f"syncRedirectDomain must be a string, got {type(value).__name__}"]
    if value == '':
        return []  # empty string means "not set"; treated as absent
    return [
        f"syncRedirectDomain: {msg}"
        for msg in check_dc_host_list(value)
    ]


def rule_sync_pub_key_domain(data: dict) -> list[str]:
    """syncPubKeyDomain must be a valid dc-pubkey-domain (RFC 8552 underscore labels + RFC 5890 domain-name)."""
    value = data.get('syncPubKeyDomain')
    if value is None:
        return []  # field is optional; absence is fine
    if not isinstance(value, str):
        return [f"syncPubKeyDomain must be a string, got {type(value).__name__}"]
    if value == '':
        return []  # empty string means "not set"; treated as absent
    if not is_valid_dc_pubkey_domain(value):
        return [f"syncPubKeyDomain is not a valid dc-pubkey-domain: {value!r}"]
    return []


def rule_display_names(data: dict) -> list[str]:
    """providerName and serviceName must be valid dc-display-name values (RFC 9839 Section 4.3)."""
    errors = []
    for field in ('providerName', 'serviceName'):
        value = data.get(field)
        if value is None:
            continue
        if not isinstance(value, str):
            errors.append(f"{field} must be a string, got {type(value).__name__}")
        elif not is_valid_dc_display_name(value):
            errors.append(f"{field} is not a valid dc-display-name: {value!r}")
    return errors


def rule_bool_fields(data: dict) -> list[str]:
    """sharedProviderName, sharedServiceName, syncBlock, multiInstance, and hostRequired must be JSON booleans."""
    errors = []
    for field in ('sharedProviderName', 'sharedServiceName', 'syncBlock', 'multiInstance', 'hostRequired', 'shared', 'warnPhishing'):
        value = data.get(field)
        if value is None:
            continue
        # In Python's json module, JSON booleans decode to bool; integers and
        # strings do not, so isinstance(value, bool) is the right check.
        if not isinstance(value, bool):
            errors.append(f"{field} must be a boolean, got {type(value).__name__}: {value!r}")
    return errors


def rule_description_texts(data: dict) -> list[str]:
    """description and variableDescription must be valid dc-description-text values (RFC 9839 Section 4.3)."""
    errors = []
    for field in ('description', 'variableDescription'):
        value = data.get(field)
        if value is None:
            continue
        if not isinstance(value, str):
            errors.append(f"{field} must be a string, got {type(value).__name__}")
        elif not is_valid_dc_description_text(value):
            errors.append(f"{field} is not a valid dc-description-text: {value!r}")
    return errors


def rule_version(data: dict) -> list[str]:
    """version, when present, must be a positive integer with no leading zeros.

    ABNF: dc-version = %x31-39 *DIGIT
    This means: 1–9 followed by zero or more digits — i.e. any integer >= 1
    with no leading zeros.  JSON parsing eliminates leading zeros syntactically,
    so the only checks needed are: is an integer, is not a bool, and is >= 1.
    """
    value = data.get('version')
    if value is None:
        return []
    # JSON booleans are a subtype of int in Python; exclude them explicitly.
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        return [f"version must be a positive integer (dc-version), got {type(value).__name__}: {value!r}"]
    return []


def rule_logo_url(data: dict) -> list[str]:
    """logoUrl, when present, must be a valid RFC 3986 URI with scheme 'https' or an empty string.

    A URI with scheme 'http' is accepted as structurally valid (it passes the
    error rule) but triggers a deprecation warning via warn_logo_url_http.
    Any other non-https scheme is an error.
    """
    value = data.get('logoUrl')
    if value is None:
        return []
    if not isinstance(value, str):
        return [f"logoUrl must be a string, got {type(value).__name__}"]
    if value == '':
        return []  # empty string means "not set"; treated as absent
    m = _URI_RE.match(value)
    if not m:
        return [f"logoUrl must be a valid https URI: {value!r}"]
    scheme = m.group(1).lower()
    if scheme not in ('https', 'http'):
        return [f"logoUrl must be a valid https URI: {value!r}"]
    return []


def rule_dc_ids(data: dict) -> list[str]:
    """providerId, serviceId, and per-record groupId must be valid dc-id values."""
    errors = []

    for field in ('providerId', 'serviceId'):
        value = data.get(field)
        if value is None:
            continue
        if not isinstance(value, str):
            errors.append(f"{field} must be a string, got {type(value).__name__}")
        elif not is_valid_dc_id(value):
            errors.append(f"{field} is not a valid dc-id: {value!r}")

    for i, record in enumerate(data.get('records', [])):
        value = record.get('groupId')
        if value is None:
            continue
        if not isinstance(value, str):
            errors.append(f"records[{i}].groupId must be a string, got {type(value).__name__}")
        elif not is_valid_dc_id(value):
            errors.append(f"records[{i}].groupId is not a valid dc-id: {value!r}")

    return errors


RULES = [
    rule_sync_redirect_domain,
    rule_sync_pub_key_domain,
    rule_display_names,
    rule_bool_fields,
    rule_description_texts,
    rule_version,
    rule_logo_url,
    rule_dc_ids,
]

# ---------------------------------------------------------------------------
# Deprecation warnings
#   Each warning check is a callable(template_data) -> list[str] of warning
#   messages.  Warnings do not affect the exit code.
# ---------------------------------------------------------------------------

def warn_shared_deprecated(data: dict) -> list[str]:
    """Warn when 'shared' is present but 'sharedProviderName' is not defined.

    'shared' is a deprecated property superseded by 'sharedProviderName';
    templates should migrate to 'sharedProviderName' instead.
    """
    if data.get('shared') is None:
        return []
    if data.get('sharedProviderName') is None:
        return [
            "DEPRECATION: 'shared' is set but 'sharedProviderName' is not defined; "
            "migrate to 'sharedProviderName'"
        ]
    return []


def warn_logo_url_http(data: dict) -> list[str]:
    """Warn when logoUrl uses the deprecated 'http' scheme instead of 'https'."""
    value = data.get('logoUrl')
    if not isinstance(value, str) or value == '':
        return []
    m = _URI_RE.match(value)
    if m and m.group(1).lower() == 'http':
        return [
            f"DEPRECATION: logoUrl uses 'http' scheme; migrate to 'https': {value!r}"
        ]
    return []


WARNINGS = [
    warn_shared_deprecated,
    warn_logo_url_http,
]

# Top-level properties explicitly covered by at least one rule above.
# Update this set whenever a new rule is added.
_CHECKED_PROPERTIES: frozenset[str] = frozenset({
    # rule_sync_redirect_domain
    'syncRedirectDomain',
    # rule_sync_pub_key_domain
    'syncPubKeyDomain',
    # rule_display_names
    'providerName',
    'serviceName',
    # rule_bool_fields
    'sharedProviderName',
    'sharedServiceName',
    'syncBlock',
    'multiInstance',
    'hostRequired',
    'shared',
    'warnPhishing',
    # rule_description_texts
    'description',
    'variableDescription',
    # rule_version
    'version',
    # rule_logo_url
    'logoUrl',
    # rule_dc_ids
    'providerId',
    'serviceId',
    # records sub-fields are handled inside rule_dc_ids (groupId),
    # but the 'records' key itself is intentionally skipped at top level.
    'records',
})


# ---------------------------------------------------------------------------
# File processing
# ---------------------------------------------------------------------------

def check_file(path: Path) -> tuple[list[str], list[str]]:
    """Load a template file and run all rules and warnings.

    Returns (errors, warnings).  A parse/IO failure is returned as a single
    error with an empty warnings list.
    """
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except json.JSONDecodeError as exc:
        return [f"JSON parse error: {exc}"], []
    except OSError as exc:
        return [f"File read error: {exc}"], []

    errors: list[str] = []
    for rule in RULES:
        errors.extend(rule(data))

    warnings: list[str] = []
    for warn in WARNINGS:
        warnings.extend(warn(data))

    return errors, warnings


def list_unchecked(folder: Path) -> int:
    """Print every top-level template property not covered by any rule.

    Collects the union of all property names found across all template files,
    subtracts _CHECKED_PROPERTIES, and prints the remainder sorted.
    'records' is always excluded (intentionally skipped at top level).
    """
    template_files = sorted(folder.glob('*.json'))
    if not template_files:
        print(f"No JSON files found in {folder}", file=sys.stderr)
        return 2

    seen: set[str] = set()
    for path in template_files:
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
        except (json.JSONDecodeError, OSError):
            continue
        seen.update(data.keys())

    unchecked = sorted(seen - _CHECKED_PROPERTIES)
    if unchecked:
        print("Top-level properties not covered by any syntax rule:")
        for prop in unchecked:
            print(f"  {prop}")
    else:
        print("All top-level properties are covered by syntax rules.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description='Check Domain Connect template files against syntax rules.'
    )
    parser.add_argument(
        '--folder',
        default='Templates',
        help="Path to templates folder (default: 'Templates')",
    )
    parser.add_argument(
        '--list-unchecked',
        action='store_true',
        help="List top-level template properties not covered by any rule, then exit.",
    )
    args = parser.parse_args()

    folder = Path(args.folder)
    if not folder.is_dir():
        print(f"Error: folder not found: {folder}", file=sys.stderr)
        return 2

    if args.list_unchecked:
        return list_unchecked(folder)

    template_files = sorted(folder.glob('*.json'))
    if not template_files:
        print(f"No JSON files found in {folder}", file=sys.stderr)
        return 2

    total = 0
    failed = 0
    warned = 0

    for path in template_files:
        total += 1
        errors, warnings = check_file(path)
        if errors:
            failed += 1
            print(f"FAIL  {path.name}")
            for err in errors:
                print(f"      {err}")
        if warnings:
            if not errors:
                warned += 1
                print(f"WARN  {path.name}")
            for w in warnings:
                print(f"      {w}")

    print(f"\n{total} files checked, {failed} with errors, {warned} with warnings only.")
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
