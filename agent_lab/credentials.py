"""Where the Jev API key comes from — without hardcoding a personal path.

The repo must not know where anyone keeps their secrets, so nothing here assumes
a location. `TYPESAFE_API_KEY` is resolved in this order, first hit wins:

  1. the process environment          already exported, or supplied by CI
  2. the repo `.env`                  gitignored; relocate with `AGENT_LAB_ENV_FILE`
  3. the file named by `TYPESAFE_ENV_FILE`   a pointer, set in `.env` or the environment

Only step 3 is a pointer, and it exists so a checkout can say "the key lives over
there" without this repository knowing where "there" is. Relative pointers resolve
against the directory of the file that declared them, so a `.env` is portable
alongside whatever it points at.

Nothing here authenticates anything. A key is read and placed in the environment,
and the TypeSafe SDK picks it up from there (`Config.resolve` reads the same
variable), which keeps credential handling in one place instead of in the caller.
"""

from __future__ import annotations

import os
from pathlib import Path

CREDENTIAL_ENV = "TYPESAFE_API_KEY"
"""The variable the TypeSafe SDK reads."""

POINTER_ENV = "TYPESAFE_ENV_FILE"
"""Names a file to read `CREDENTIAL_ENV` from, when it is not set directly."""

DOTENV_ENV = "AGENT_LAB_ENV_FILE"
"""Relocates the repo `.env`. Useful for tests and for out-of-tree checkouts."""

REPO_ROOT = Path(__file__).resolve().parent.parent


class CredentialError(RuntimeError):
    """No key could be resolved, and the message says exactly how to fix that."""


# --------------------------------------------------------------------------
# Reading env files. A deliberate subset of dotenv syntax, no dependency.
# --------------------------------------------------------------------------


def parse_env_file(text: str) -> dict[str, str]:
    """Parse the subset of dotenv syntax this lab needs.

    Blank lines and `#` comments are skipped, an `export ` prefix is tolerated,
    and single or double quotes around a value are stripped. There is no
    interpolation and no `${...}` expansion: a value is exactly what is written,
    so reading a file cannot execute anything or surprise you.
    """
    values: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        key, sep, value = line.partition("=")
        if not sep:
            continue
        key = key.strip()
        if not key:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key] = value
    return values


def read_env_file(path: Path) -> dict[str, str]:
    """Values from `path`. An absent file is empty, not an error.

    A missing `.env` is the normal case for a fresh clone, so only a pointer that
    was explicitly configured and then does not contain the key is an error.
    """
    try:
        return parse_env_file(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}


def repo_env_path() -> Path:
    """The repo `.env`, unless `AGENT_LAB_ENV_FILE` relocates it."""
    override = os.environ.get(DOTENV_ENV)
    if override:
        return Path(override).expanduser()
    return REPO_ROOT / ".env"


def _resolve_pointer(value: str, base: Path) -> Path:
    """`~` expands, and a relative pointer resolves against the file declaring it."""
    pointer = Path(value).expanduser()
    return pointer if pointer.is_absolute() else base / pointer


# --------------------------------------------------------------------------
# The one function callers need.
# --------------------------------------------------------------------------


def load_typesafe_credentials() -> str:
    """Resolve `TYPESAFE_API_KEY`, export it, and return it.

    Raises `CredentialError` when no source yields a key. The key is never
    returned in an error message, and a value already in the environment is
    never overwritten.
    """
    already_set = os.environ.get(CREDENTIAL_ENV)
    if already_set:
        return already_set

    dotenv = repo_env_path()
    dotenv_values = read_env_file(dotenv)

    direct = dotenv_values.get(CREDENTIAL_ENV)
    if direct:
        os.environ[CREDENTIAL_ENV] = direct
        return direct

    pointer = dotenv_values.get(POINTER_ENV) or os.environ.get(POINTER_ENV)
    if pointer:
        target = _resolve_pointer(pointer, dotenv.parent)
        pointed = read_env_file(target).get(CREDENTIAL_ENV)
        if pointed:
            os.environ[CREDENTIAL_ENV] = pointed
            return pointed
        raise CredentialError(
            f"{POINTER_ENV} points at {target}, but that file does not set "
            f"{CREDENTIAL_ENV}."
        )

    raise CredentialError(
        f"{CREDENTIAL_ENV} is not set. Pick one:\n"
        f"  1. export {CREDENTIAL_ENV}=...            for this shell\n"
        f"  2. cp .env.example .env                  and set {CREDENTIAL_ENV} in it\n"
        f"  3. cp .env.example .env                  and set {POINTER_ENV} to the\n"
        f"     path of an existing file that already sets {CREDENTIAL_ENV}"
    )
