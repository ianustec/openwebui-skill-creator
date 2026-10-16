"""
title: Skill Creator
author: IANUSTEC
author_url: https://ianustec.com
funding_url: https://github.com/ianustec/openwebui-skill-creator
description: Create and save Open WebUI Workspace Skills from SKILL.md (Claude-style) — interview, guidelines, draft, validate, persist private by default
requirements: httpx, pydantic, PyYAML
required_open_webui_version: 0.10.0
version: 1.3.0
license: MIT
"""

# ============================================================================
# Skill Creator — open-source Open WebUI tool
# ----------------------------------------------------------------------------
# Guides the model to design a Workspace Skill (Claude skill-creator style)
# and PERSISTS it via POST /api/v1/skills/create (private by default).
#
# Do NOT only dump SKILL.md in chat — always call create_skill to save it.
#
# License: MIT — Copyright (c) 2026 IANUSTEC.
# ============================================================================

from __future__ import annotations

import json
import re
import traceback
import unicodedata
from typing import Any, Optional
from urllib.parse import quote

from pydantic import BaseModel, Field

try:
    import httpx  # type: ignore

    _HAS_HTTPX = True
except ImportError:
    httpx = None  # type: ignore[assignment]
    _HAS_HTTPX = False

try:
    import yaml  # type: ignore

    _HAS_YAML = True
except ImportError:
    yaml = None  # type: ignore[assignment]
    _HAS_YAML = False


# ============================================================================
# Skill writing guidelines (adapted from Anthropic skill-creator best practices)
# ============================================================================

_SKILL_GUIDELINES = """\
# How to Write a Great SKILL.md

## What a skill is (and is not)

A skill is a reusable procedure the model follows when triggered — the "how"
(runbook, formats, criteria). It is NOT an agent/model: a Workspace Model is
the "who" (identity, system prompt, base LLM, bound tools/knowledge/skills).
One model can bind many skills; one skill can be reused by many models.

Activation: `$skill-name` in chat, binding under Workspace → Models → Skills,
or automatic selection when the description matches the request.

A good skill reads like a well-written runbook a smart colleague could follow
— not a bare checklist. Use this structure unless the user asks otherwise.

## Frontmatter

- **name** (required): kebab-case identifier, e.g. `consulta-codici-fiscali`.
- **description** (required): the PRIMARY triggering mechanism. It must state
  BOTH what the skill does AND when to use it. Be deliberately "pushy" —
  models tend to under-trigger skills, so list concrete trigger phrases,
  synonyms, and near-miss contexts. Example:
  "Answers questions about fiscal and civil codes using ONLY the /CODICI/
  folder. Use whenever the user asks for legal references, article numbers,
  TUIR, IVA rules, 'cosa dice il codice', 'articolo X', or normativa — even
  if they don't say 'skill' or 'codici'."
- **tags** (optional): short labels for grouping.

## Recommended body sections

Adapt names to the domain, but aim to cover all of these:

1. **Purpose / When to use** — one short paragraph. What outcome the user
   gets, and the situations where this skill applies (and where it does NOT).
2. **Inputs** — what the user is expected to provide (question, file, data,
   period, entity…). Note what to do when inputs are missing: ask, don't guess.
3. **Procedure** — numbered steps. For each step say WHAT to do and WHY it
   matters (models follow reasoning better than bare MUSTs). If the skill
   calls tools, name the exact tool and its key arguments, and describe how
   to handle empty/error results.
4. **Rules & constraints** — the hard boundaries (e.g. "only use files under
   /CODICI/", "never invent article numbers"). Explain WHY each rule exists
   so the model can apply judgment at the edges instead of blindly obeying.
5. **Output format** — an exact template the model must fill, e.g.:

   ## Answer template
   ALWAYS answer with this structure:
   - **Risposta** — direct answer in 2-4 sentences
   - **Riferimento** — article/norm with exact citation
   - **Fonte** — source file with link
   - **Note** — caveats or related provisions (optional)

6. **Examples** — at least one, ideally two, worked input/output pairs:

   **Example 1**
   Input: "Cosa dice il TUIR sulle plusvalenze?"
   Output: **Risposta** … **Riferimento** Art. 68 TUIR … **Fonte** …

7. **Edge cases & failure handling** — what to do when: nothing is found
   ("say you found nothing in the authorized sources, don't improvise"),
   the question is out of scope, the input is ambiguous (ask one targeted
   clarifying question), or a tool errors.
8. **Quality checklist** (optional but valuable) — 3-5 questions the model
   asks itself before answering: "Did I only use authorized sources? Did I
   cite the exact article? Is the output in the required template?"

## Style rules

- Imperative mood ("Search…", "Filter…", "Answer with…"), not descriptive.
- Explain the WHY behind important steps instead of stacking ALL-CAPS MUSTs —
  a model that understands the reason generalizes; a model with only rigid
  rules breaks on the first unexpected input.
- Keep the body under ~500 lines. If you need more, split detail into clear
  sections with headings so the model can navigate.
- Write in the user's language (match the skill's domain language).
- Do not include secrets, internal URLs, or environment-specific paths unless
  the user explicitly provides them — and then mark them clearly.

## Before saving

Run through this checklist mentally and fix the draft if anything is missing:
- [ ] description states WHAT + WHEN (with trigger phrases)
- [ ] procedure steps have a reason, not just an order
- [ ] output format is an explicit template
- [ ] at least one worked example (input → output)
- [ ] failure/empty-result behavior is defined
- [ ] rules explain why, not only what
"""

# Keep historical EN/IT substring matches; new French aliases are word-bounded.
_GUIDELINES_SECTION_HINTS = (
    ("purpose", ("purpose", "when to use", "scopo", "quando usar", "quando usare")),
    ("procedure", ("procedure", "step", "workflow", "process", "procedura")),
    ("output format", ("output", "template", "format", "struttura")),
    ("examples", ("example", "esempio", "esempi", "input:", "output:")),
    ("edge cases", ("edge case", "error", "fallback", "not found",
                    "non trov", "fuori scope", "out of scope", "failure")),
)

# Avoid ambiguous English words such as "but" and "structure". List plurals
# explicitly so "modèle"/"modèles" work without accepting English "modeled".
_GUIDELINES_FR_SECTION_HINTS = {
    "purpose": ("objectif", "objectifs", "finalité", "finalités",
                "quand l'utiliser", "quand l’utiliser", "quand utiliser",
                "déclencheur", "déclencheurs"),
    "procedure": ("procédure", "procédures", "étape", "étapes", "méthode",
                  "méthodes", "déroulé", "déroulés", "processus"),
    # Bare "sortie"/"entrée" are also English nouns (sortie, entree).
    "output format": ("format de sortie", "sortie attendue", "sorties attendues",
                      "modèle", "modèles", "gabarit", "gabarits"),
    "examples": ("exemple", "exemples"),
    "edge cases": ("cas limite", "cas limites", "erreur", "erreurs",
                   "hors périmètre", "si rien n'est trouvé", "échec", "échecs",
                   "cas particulier", "cas particuliers"),
}


def _normalize_guideline_text(text: str) -> str:
    """Match guideline hints without case or combining-accent differences."""
    return "".join(
        c for c in unicodedata.normalize("NFKD", text.lower())
        if not unicodedata.combining(c)
    )


def _has_french_hint(text: str, hints: tuple[str, ...]) -> bool:
    """Match whole hints in already-normalized text, not inside other words."""
    return any(
        re.search(r"(?<!\w)" + re.escape(_normalize_guideline_text(h)) + r"(?!\w)", text)
        for h in hints
    )


def _analyze_skill_body(body: str) -> list[str]:
    """Soft quality warnings using accent-insensitive EN/IT/FR hints."""
    low = _normalize_guideline_text(body or "")
    warnings: list[str] = []
    if not low.strip():
        return warnings
    for label, hints in _GUIDELINES_SECTION_HINTS:
        if not any(h in low for h in hints) and not _has_french_hint(
            low, _GUIDELINES_FR_SECTION_HINTS[label]
        ):
            warnings.append(f"no clear '{label}' section")
    if not any(h in low for h in ("example", "esempi", "esempio")) and not _has_french_hint(
        low, ("exemple", "exemples")
    ):
        warnings.append("no worked example (input → output)")
    return warnings


# ============================================================================
# Parsing & validation (Claude skill-creator quick_validate, adapted for OWUI)
# ============================================================================

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?", re.DOTALL)
_ALLOWED_FM_KEYS = {
    "name",
    "description",
    "license",
    "allowed-tools",
    "metadata",
    "compatibility",
    "title",
    "display_name",
    "tags",
}


def _slugify_id(text: str, *, max_len: int = 64) -> str:
    """Kebab-case skill id (lowercase, digits, hyphens)."""
    t = unicodedata.normalize("NFKD", text or "")
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = t.lower().strip()
    t = re.sub(r"[^a-z0-9]+", "-", t)
    t = re.sub(r"-{2,}", "-", t).strip("-")
    return (t or "skill")[:max_len]


def _display_name_from_id(skill_id: str) -> str:
    """Human title from kebab-case id."""
    return " ".join(part.capitalize() for part in (skill_id or "skill").split("-") if part)


def _strip_code_fence(text: str) -> str:
    t = (text or "").strip()
    if t.startswith("```"):
        t = re.sub(r"^```(?:markdown|md|yaml|skill)?\s*", "", t)
        t = re.sub(r"\s*```\s*$", "", t)
    return t.strip()


def _yaml_load(text: str) -> dict:
    if not text or not text.strip():
        return {}
    if not _HAS_YAML:
        # Minimal fallback: key: value lines only
        out: dict = {}
        for line in text.splitlines():
            if ":" not in line or line.strip().startswith("#"):
                continue
            k, _, v = line.partition(":")
            k, v = k.strip(), v.strip().strip("\"'")
            if k:
                out[k] = v
        return out
    try:
        data = yaml.safe_load(text)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _parse_skill_content(content: Any) -> dict:
    """Accept SKILL.md markdown, JSON string, or dict → normalized spec.

    Spec keys: id, name, description, content (body), tags (list), meta (dict).
    """
    if isinstance(content, dict):
        raw = dict(content)
        skill_id = _slugify_id(
            str(raw.get("id") or raw.get("name") or "skill")
        )
        display = (
            raw.get("title")
            or raw.get("display_name")
            or raw.get("name")
            or _display_name_from_id(skill_id)
        )
        body = raw.get("content") or raw.get("body") or raw.get("instructions") or ""
        tags = raw.get("tags") or (raw.get("meta") or {}).get("tags") or []
        if isinstance(tags, str):
            tags = [t.strip() for t in tags.split(",") if t.strip()]
        return {
            "id": skill_id,
            "name": str(display).strip() or _display_name_from_id(skill_id),
            "description": (raw.get("description") or "").strip(),
            "content": str(body).strip(),
            "tags": list(tags) if isinstance(tags, list) else [],
        }

    if not isinstance(content, str):
        raise ValueError(f"unsupported content type: {type(content).__name__}")

    text = _strip_code_fence(content)
    if not text:
        raise ValueError("empty content")

    # JSON object?
    head = text.lstrip()
    if head.startswith("{") or head.startswith("["):
        try:
            parsed = json.loads(text)
            return _parse_skill_content(parsed)
        except json.JSONDecodeError:
            pass

    # Markdown with YAML frontmatter
    fm: dict = {}
    body = text
    m = _FRONTMATTER_RE.match(text)
    if m:
        fm = _yaml_load(m.group(1))
        body = text[m.end() :].lstrip("\n")
    elif text.startswith("---"):
        raise ValueError("invalid YAML frontmatter (unclosed ---)")

    name_raw = str(fm.get("name") or "").strip()
    if not name_raw:
        # Try first H1 as name
        h1 = re.search(r"^#\s+(.+)$", body, re.MULTILINE)
        name_raw = h1.group(1).strip() if h1 else "untitled-skill"

    skill_id = _slugify_id(name_raw)
    display = (
        str(fm.get("title") or fm.get("display_name") or "").strip()
        or _display_name_from_id(skill_id)
    )
    description = str(fm.get("description") or "").strip()
    tags = fm.get("tags") or []
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",") if t.strip()]
    if not isinstance(tags, list):
        tags = []

    return {
        "id": skill_id,
        "name": display,
        "description": description,
        "content": body.strip(),
        "tags": tags,
        "_frontmatter_keys": list(fm.keys()),
    }


def _validate_skill_spec(spec: dict) -> tuple[bool, list[str]]:
    """Validate a normalized skill spec. Returns (ok, errors)."""
    errors: list[str] = []

    # Unexpected frontmatter keys (when parsed from markdown)
    fm_keys = spec.get("_frontmatter_keys")
    if fm_keys:
        unexpected = set(fm_keys) - _ALLOWED_FM_KEYS
        if unexpected:
            errors.append(
                "Unexpected frontmatter key(s): "
                + ", ".join(sorted(unexpected))
                + f". Allowed: {', '.join(sorted(_ALLOWED_FM_KEYS))}"
            )

    skill_id = (spec.get("id") or "").strip()
    if not skill_id:
        errors.append("Missing skill id / name")
    else:
        if not re.match(r"^[a-z0-9-]+$", skill_id):
            errors.append(
                f"Id '{skill_id}' must be kebab-case "
                "(lowercase letters, digits, hyphens only)"
            )
        if skill_id.startswith("-") or skill_id.endswith("-") or "--" in skill_id:
            errors.append(
                f"Id '{skill_id}' cannot start/end with hyphen "
                "or contain consecutive hyphens"
            )
        if len(skill_id) > 64:
            errors.append(f"Id too long ({len(skill_id)} chars). Max 64.")

    name = (spec.get("name") or "").strip()
    if not name:
        errors.append("Missing display name")

    description = (spec.get("description") or "").strip()
    if not description:
        errors.append(
            "Missing description — required for skill triggering "
            "(include what it does AND when to use it)"
        )
    else:
        if "<" in description or ">" in description:
            errors.append("Description cannot contain angle brackets (< or >)")
        if len(description) > 1024:
            errors.append(
                f"Description too long ({len(description)} chars). Max 1024."
            )

    body = (spec.get("content") or "").strip()
    if not body:
        errors.append("Missing skill body (instructions markdown)")
    elif body.count("\n") + 1 > 800:
        errors.append(
            "Body is very long (>800 lines). Prefer <500 lines and "
            "split detail into clear sections."
        )

    return (len(errors) == 0, errors)


def _validate_with_warnings(spec: dict) -> tuple[bool, list[str], list[str]]:
    """Hard validation + soft quality warnings (missing recommended sections)."""
    ok, errors = _validate_skill_spec(spec)
    warnings = _analyze_skill_body(spec.get("content") or "")
    return ok, errors, warnings


def _to_skill_form(spec: dict, *, is_active: bool, private: bool) -> dict:
    """Build OpenWebUI SkillForm JSON payload."""
    tags = spec.get("tags") or []
    if not isinstance(tags, list):
        tags = []
    payload: dict[str, Any] = {
        "id": spec["id"],
        "name": spec["name"],
        "description": spec.get("description") or "",
        "content": spec.get("content") or "",
        "meta": {"tags": tags},
        "is_active": bool(is_active),
    }
    # OWUI SkillForm rejects JSON null for access_grants (422 list_type).
    # Empty list = private (owner-only); never send null.
    payload["access_grants"] = [] if private else (payload.get("access_grants") or [])
    return payload


# ============================================================================
# Auth + Skills HTTP client
# ============================================================================


def _extract_bearer(request) -> Optional[str]:
    """Pull the user JWT from the OpenWebUI Request."""
    if request is None:
        return None

    # 1) request.state.token (OWUI injects this for authenticated requests)
    tok = getattr(getattr(request, "state", None), "token", None)
    creds = getattr(tok, "credentials", None) if tok else None
    if creds:
        return str(creds)

    # 2) Authorization header
    try:
        headers = getattr(request, "headers", None)
        if headers is not None:
            auth = headers.get("authorization") or headers.get("Authorization")
            if auth and isinstance(auth, str):
                if auth.lower().startswith("bearer "):
                    return auth[7:].strip()
                return auth.strip()
            # 3) Cookie "token"
            cookie = headers.get("cookie") or headers.get("Cookie") or ""
            m = re.search(r"(?:^|;\s*)token=([^;]+)", cookie)
            if m:
                return m.group(1).strip()
    except Exception:
        traceback.print_exc()

    # 4) request.cookies
    try:
        cookies = getattr(request, "cookies", None)
        if cookies and cookies.get("token"):
            return str(cookies.get("token"))
    except Exception:
        pass

    return None


def _api_base(request, override: str = "") -> str:
    o = (override or "").strip().rstrip("/")
    if o:
        return o
    if request is not None:
        try:
            return str(request.base_url).rstrip("/")
        except Exception:
            pass
    return ""


async def _skills_api(
    *,
    request,
    method: str,
    path: str,
    json_body: Optional[dict] = None,
    api_base_override: str = "",
) -> tuple[int, Any]:
    """Call /api/v1/skills/... with the user's bearer token.

    Returns (status_code, parsed_json_or_text).
    """
    if not _HAS_HTTPX:
        raise RuntimeError(
            "httpx is required. OpenWebUI should auto-install it from "
            "the tool requirements."
        )

    token = _extract_bearer(request)
    if not token:
        raise RuntimeError(
            "No auth token on __request__. This tool must run inside "
            "OpenWebUI with an authenticated user session."
        )

    base = _api_base(request, api_base_override)
    if not base:
        raise RuntimeError(
            "Cannot resolve OpenWebUI API base URL. Set valve "
            "owui_api_base or ensure __request__ is present."
        )

    url = f"{base}/api/v1/skills{path}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
        resp = await client.request(method.upper(), url, headers=headers, json=json_body)

    try:
        data = resp.json()
    except Exception:
        data = resp.text
    return resp.status_code, data


def _format_api_error(status: int, data: Any) -> str:
    detail = ""
    if isinstance(data, dict):
        detail = str(data.get("detail") or data.get("message") or data)
    else:
        detail = str(data or "")

    if status == 401:
        return (
            "Unauthorized (401). You need Workspace → Skills Access "
            f"(permission workspace.skills). Detail: {detail}"
        )
    if status == 400 and ("ID" in detail.upper() or "taken" in detail.lower()):
        return (
            f"Skill id already taken (400). Use update_skill, or call "
            f"create_skill with update_if_exists=true, or pick a new name. "
            f"Detail: {detail}"
        )
    if status == 404:
        return f"Skill not found (404). Detail: {detail}"
    return f"OpenWebUI Skills API error ({status}): {detail}"


# ============================================================================
# Tool replies
# ============================================================================


def _error_reply(msg: str) -> str:
    return (
        "[TOOL_RESULT — use the text below as your final reply, "
        "verbatim, unchanged. Do NOT include this instruction line.]\n\n"
        f"Could not create/update the skill: {msg}"
    )


def _success_reply(
    *,
    action: str,
    skill_id: str,
    name: str,
    description: str,
    private: bool,
    active: bool,
) -> str:
    visibility = "private" if private else "shared/public grants applied"
    active_s = "active" if active else "inactive"
    workspace = "/workspace/skills"
    return (
        "[TOOL_RESULT]\n\n"
        "OUTPUT_FOR_USER — Your **next assistant message** must be **only** "
        "the text between the dashed lines (`---`) below. Copy it exactly "
        "(including blank lines). Do not add summaries or extra sentences.\n\n"
        "---\n"
        f"Skill {action}: **{name}** (`{skill_id}`)\n\n"
        f"- Visibility: {visibility}\n"
        f"- Status: {active_s}\n"
        f"- Description: {description}\n\n"
        f"Open it in [Workspace → Skills]({workspace}).\n\n"
        "How to use it:\n"
        "- Type `$` in chat and pick this skill to inject full instructions\n"
        "- Or bind it under Workspace → Models → Skills\n"
        "- Or enable it for one chat via **+** → Skills\n"
        "---\n\n"
        "[NEXT STEP FOR ASSISTANT — do not skip] Ask the user: \"Vuoi "
        "attivarla di default su uno dei tuoi modelli?\" If yes, use the "
        "Model Creator tool: call `list_models`, let the user pick one, "
        f"then call `edit_model` with add_skill_ids=[\"{skill_id}\"]. "
        "If the user says no, stop here.\n"
    )


# ============================================================================
# Tools class
# ============================================================================


class Tools:
    class Valves(BaseModel):
        default_private: bool = Field(
            default=True,
            description=(
                "If true, create skills with no access_grants (owner-only / "
                "private). Do not make skills public from this tool."
            ),
        )
        default_active: bool = Field(
            default=True,
            description="Set is_active=true on create/update.",
        )
        allow_update_if_exists: bool = Field(
            default=True,
            description=(
                "When create_skill(..., update_if_exists=true) and the id "
                "already exists, fall back to update instead of failing."
            ),
        )
        enforce_guidelines: bool = Field(
            default=True,
            description=(
                "Quality gate: if the draft is missing recommended sections "
                "(purpose, procedure, output format, examples, edge cases), "
                "create_skill returns the writing guidelines and asks for a "
                "better draft instead of saving. Pass force=true to bypass."
            ),
        )
        emit_status: bool = Field(
            default=True,
            description="Emit progress status events in chat.",
        )
        owui_api_base: str = Field(
            default="",
            description=(
                "Optional override for Open WebUI base URL. "
                "Empty = use request.base_url."
            ),
        )

    def __init__(self):
        self.valves = self.Valves()
        self.citation = False
        self.tools = [
            self._descriptor_create(),
            self._descriptor_update(),
            self._descriptor_validate(),
            self._descriptor_guidelines(),
        ]

    # ── Status helpers ───────────────────────────────────────────────────────
    async def _emit_status(self, emitter, description: str, *, done: bool) -> None:
        if not emitter or not self.valves.emit_status:
            return
        try:
            await emitter({
                "type": "status",
                "data": {"description": description, "done": done},
            })
        except Exception:
            pass

    async def _emit_message(self, emitter, text: str) -> None:
        if not emitter:
            return
        try:
            await emitter({"type": "message", "data": {"content": text}})
        except Exception:
            pass

    # ── Public: get_skill_guidelines ─────────────────────────────────────────
    async def get_skill_guidelines(
        self,
        __event_emitter__=None,
        __request__=None,
        __user__=None,
    ) -> str:
        """Return the official skill-writing knowledge: what a skill is, how
        it differs from a model/agent, the full SKILL.md structure, and the
        style rules. Call this for ANY question about skills — and always
        before drafting one — so the answer follows the official guidelines
        instead of improvising."""
        return (
            "[TOOL_RESULT — these are the official skill guidelines. Base "
            "your answer (or your draft) on them. Do NOT paste them verbatim "
            "unless the user asks to see the full guidelines.]\n\n"
            + _SKILL_GUIDELINES
        )

    # ── Public: validate_skill ───────────────────────────────────────────────
    async def validate_skill(
        self,
        content: str,
        __event_emitter__=None,
        __request__=None,
        __user__=None,
    ) -> str:
        """Validate a SKILL.md (or JSON) draft without saving.

        Checks frontmatter and reports which recommended sections are missing
        (purpose, procedure, output format, examples, edge cases).
        Does NOT write to OpenWebUI.

        Args:
            content: SKILL.md with YAML frontmatter, or a JSON object string
                with id/name/description/content.

        Returns:
            Validation result for the user.
        """
        try:
            spec = _parse_skill_content(content)
        except Exception as exc:
            return _error_reply(f"Parse error: {exc}")

        ok, errors, warnings = _validate_with_warnings(spec)
        if not ok:
            return (
                "[TOOL_RESULT — use the text below as your final reply, "
                "verbatim.]\n\n"
                "Skill draft is **invalid**:\n"
                + "\n".join(f"- {e}" for e in errors)
            )
        warn_block = ""
        if warnings:
            warn_block = (
                "\n**Completeness warnings** (skill works but may feel "
                "schematic — consider adding):\n"
                + "\n".join(f"- {w}" for w in warnings)
                + "\n\nCall `get_skill_guidelines` for the recommended "
                "structure, then improve the draft before saving.\n"
            )
        return (
            "[TOOL_RESULT — use the text below as your final reply, "
            "verbatim.]\n\n"
            f"Skill draft is **valid**.\n\n"
            f"- id: `{spec['id']}`\n"
            f"- name: {spec['name']}\n"
            f"- description: {spec['description'][:200]}"
            f"{'…' if len(spec['description']) > 200 else ''}\n"
            f"- body lines: {spec['content'].count(chr(10)) + 1}\n"
            f"{warn_block}\n"
            "Call `create_skill` with the same content to save it "
            "(private by default)."
        )

    # ── Public: create_skill ─────────────────────────────────────────────────
    async def create_skill(
        self,
        content: str,
        update_if_exists: bool = False,
        force: bool = False,
        __event_emitter__=None,
        __request__=None,
        __user__=None,
    ) -> str:
        """Create (and persist) an Open WebUI Workspace Skill from SKILL.md.

        ALWAYS call this after drafting a skill — do NOT only paste markdown
        in chat. The skill is saved private by default (owner only).

        WORKFLOW (skill-creator style):
        1. Interview the user: what should the skill enable? when should it
           trigger? expected output format? edge cases?
        2. Call get_skill_guidelines() to load the recommended SKILL.md
           structure (purpose, procedure, output format, examples, edge
           cases) — a bare checklist makes a schematic, weak skill.
        3. Draft SKILL.md following those guidelines: pushy description
           (what + when + trigger phrases), imperative body with reasons,
           an explicit output template, at least one worked example, and
           failure/empty-result behavior.
        4. Optionally call validate_skill — it also reports missing sections.
        5. Call THIS tool to save. Confirm the returned id to the user.

        Example content::

            ---
            name: code-review-guidelines
            description: Step-by-step code review checklist. Use whenever the
              user asks for a code review, PR feedback, or to check a diff —
              even if they do not say "skill" or "guidelines".
            ---

            # Code Review Guidelines

            ## Purpose
            ...

            ## Procedure
            1. Check correctness and edge cases — because ...
            2. Verify tests and error handling — because ...

            ## Output format
            ...

            ## Examples
            ...

        Args:
            content: SKILL.md with YAML frontmatter (preferred) OR JSON
                ``{"id","name","description","content"}``.
            update_if_exists: If true and the id already exists, update that
                skill instead of failing (requires write access).
            force: Bypass the quality gate (save even if the draft is
                missing recommended sections).

        Returns:
            Confirmation with skill id and how to use it ($ mention / bind),
            or the writing guidelines if the draft is too schematic.
        """
        await self._emit_status(
            __event_emitter__, "Parsing skill draft...", done=False
        )
        try:
            spec = _parse_skill_content(content)
        except Exception as exc:
            return _error_reply(f"Parse error: {exc}")

        ok, errors, warnings = _validate_with_warnings(spec)
        if not ok:
            return _error_reply(
                "Validation failed:\n" + "\n".join(f"- {e}" for e in errors)
            )

        # Quality gate: a schematic draft is NOT saved — the model must load
        # the guidelines and improve it first (or explicitly force).
        if warnings and self.valves.enforce_guidelines and not force:
            await self._emit_status(
                __event_emitter__,
                "Draft too schematic — returning guidelines...",
                done=True,
            )
            return (
                "[TOOL_RESULT — the skill was NOT saved. Improve the draft "
                "using the guidelines below, then call create_skill again "
                "with the improved content. Tell the user briefly what you "
                "are adding. Only use force=true if the user explicitly "
                "asked for a minimal skill.]\n\n"
                "The draft is missing recommended sections:\n"
                + "\n".join(f"- {w}" for w in warnings)
                + "\n\n"
                + _SKILL_GUIDELINES
            )

        private = bool(self.valves.default_private)
        active = bool(self.valves.default_active)
        form = _to_skill_form(spec, is_active=active, private=private)

        await self._emit_status(
            __event_emitter__,
            f"Saving skill `{spec['id']}` to Workspace...",
            done=False,
        )

        try:
            status, data = await _skills_api(
                request=__request__,
                method="POST",
                path="/create",
                json_body=form,
                api_base_override=self.valves.owui_api_base,
            )
        except Exception as exc:
            traceback.print_exc()
            return _error_reply(str(exc))

        # ID taken → optional update
        if status == 400 and update_if_exists and self.valves.allow_update_if_exists:
            await self._emit_status(
                __event_emitter__,
                f"Id taken — updating existing skill `{spec['id']}`...",
                done=False,
            )
            return await self._do_update(
                skill_id=spec["id"],
                form=form,
                spec=spec,
                private=private,
                active=active,
                __event_emitter__=__event_emitter__,
                __request__=__request__,
            )

        if status not in (200, 201):
            await self._emit_status(__event_emitter__, "Save failed.", done=True)
            return _error_reply(_format_api_error(status, data))

        await self._emit_status(__event_emitter__, "Skill saved.", done=True)
        reply = _success_reply(
            action="created",
            skill_id=spec["id"],
            name=spec["name"],
            description=spec["description"],
            private=private,
            active=active,
        )
        if warnings:
            reply += (
                "\n[NOTE FOR ASSISTANT — mention briefly to the user] The "
                "saved skill is missing recommended sections: "
                + "; ".join(warnings)
                + ". Offer to improve it with update_skill after loading "
                "get_skill_guidelines.\n"
            )
        await self._emit_message(
            __event_emitter__,
            f"\n\nSkill created: **{spec['name']}** (`{spec['id']}`) — "
            f"[Workspace → Skills](/workspace/skills)\n",
        )
        return reply

    # ── Public: update_skill ─────────────────────────────────────────────────
    async def update_skill(
        self,
        skill_id: str,
        content: str,
        __event_emitter__=None,
        __request__=None,
        __user__=None,
    ) -> str:
        """Update an existing Open WebUI Workspace Skill you own (or can write).

        Args:
            skill_id: Existing skill id (kebab-case slug).
            content: New SKILL.md or JSON body (same formats as create_skill).
                Frontmatter name/description override the stored metadata;
                the path id wins for the resource id.

        Returns:
            Confirmation that the skill was updated.
        """
        sid = _slugify_id(skill_id or "")
        if not sid:
            return _error_reply("skill_id is required")

        await self._emit_status(
            __event_emitter__, f"Updating skill `{sid}`...", done=False
        )
        try:
            spec = _parse_skill_content(content)
        except Exception as exc:
            return _error_reply(f"Parse error: {exc}")

        # Keep the path id authoritative
        spec["id"] = sid
        ok, errors = _validate_skill_spec(spec)
        if not ok:
            return _error_reply(
                "Validation failed:\n" + "\n".join(f"- {e}" for e in errors)
            )

        private = bool(self.valves.default_private)
        active = bool(self.valves.default_active)
        form = _to_skill_form(spec, is_active=active, private=private)
        # Update endpoint still expects SkillForm including id
        form["id"] = sid

        return await self._do_update(
            skill_id=sid,
            form=form,
            spec=spec,
            private=private,
            active=active,
            __event_emitter__=__event_emitter__,
            __request__=__request__,
        )

    async def _do_update(
        self,
        *,
        skill_id: str,
        form: dict,
        spec: dict,
        private: bool,
        active: bool,
        __event_emitter__,
        __request__,
    ) -> str:
        try:
            status, data = await _skills_api(
                request=__request__,
                method="POST",
                path=f"/id/{quote(skill_id, safe='')}/update",
                json_body=form,
                api_base_override=self.valves.owui_api_base,
            )
        except Exception as exc:
            traceback.print_exc()
            return _error_reply(str(exc))

        if status not in (200, 201):
            await self._emit_status(__event_emitter__, "Update failed.", done=True)
            return _error_reply(_format_api_error(status, data))

        await self._emit_status(__event_emitter__, "Skill updated.", done=True)
        reply = _success_reply(
            action="updated",
            skill_id=skill_id,
            name=spec["name"],
            description=spec["description"],
            private=private,
            active=active,
        )
        await self._emit_message(
            __event_emitter__,
            f"\n\nSkill updated: **{spec['name']}** (`{skill_id}`)\n",
        )
        return reply

    # ── Tool descriptors (native function calling) ───────────────────────────
    @staticmethod
    def _descriptor_create() -> dict:
        return {
            "type": "function",
            "function": {
                "name": "create_skill",
                "description": (
                    "Create and SAVE an Open WebUI Workspace Skill from "
                    "SKILL.md (YAML frontmatter + markdown body). "
                    "Private by default. ALWAYS call this after drafting — "
                    "never only paste SKILL.md in chat.\n\n"
                    "MANDATORY WORKFLOW: (1) interview the user — what it "
                    "does, when to trigger, output format, edge cases; "
                    "(2) call get_skill_guidelines to load the recommended "
                    "structure; (3) draft a COMPLETE SKILL.md: pushy "
                    "description (what + when + trigger phrases), purpose, "
                    "procedure with reasons, explicit output template, at "
                    "least one worked example, edge-case/failure behavior; "
                    "(4) call this tool.\n\n"
                    "QUALITY GATE: if the draft is missing recommended "
                    "sections, this tool does NOT save — it returns the "
                    "guidelines and you must improve the draft and call "
                    "again. A bare checklist makes a schematic, weak skill.\n\n"
                    "Example:\n"
                    "---\n"
                    "name: my-skill\n"
                    "description: Does X. Use whenever the user mentions Y...\n"
                    "---\n\n"
                    "# Instructions\n..."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "content": {
                            "type": "string",
                            "description": (
                                "Full SKILL.md with YAML frontmatter "
                                "(name + description required) and markdown "
                                "body, OR a JSON object string with "
                                "id/name/description/content."
                            ),
                        },
                        "update_if_exists": {
                            "type": "boolean",
                            "description": (
                                "If true and the skill id already exists, "
                                "update it instead of failing."
                            ),
                            "default": False,
                        },
                        "force": {
                            "type": "boolean",
                            "description": (
                                "Bypass the quality gate and save even if "
                                "the draft is missing recommended sections. "
                                "Only when the user explicitly wants a "
                                "minimal skill."
                            ),
                            "default": False,
                        },
                    },
                    "required": ["content"],
                },
            },
        }

    @staticmethod
    def _descriptor_update() -> dict:
        return {
            "type": "function",
            "function": {
                "name": "update_skill",
                "description": (
                    "Update an existing Open WebUI Workspace Skill by id "
                    "with a new SKILL.md / JSON body. Use after iterating "
                    "on a draft that was already saved."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "skill_id": {
                            "type": "string",
                            "description": "Existing skill id (kebab-case).",
                        },
                        "content": {
                            "type": "string",
                            "description": "New SKILL.md or JSON content.",
                        },
                    },
                    "required": ["skill_id", "content"],
                },
            },
        }

    @staticmethod
    def _descriptor_validate() -> dict:
        return {
            "type": "function",
            "function": {
                "name": "validate_skill",
                "description": (
                    "Validate a SKILL.md draft (frontmatter + body) without "
                    "saving. Also reports which recommended sections are "
                    "missing (purpose, procedure, output format, examples, "
                    "edge cases). Call before create_skill."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "content": {
                            "type": "string",
                            "description": "SKILL.md or JSON draft to validate.",
                        },
                    },
                    "required": ["content"],
                },
            },
        }

    @staticmethod
    def _descriptor_guidelines() -> dict:
        return {
            "type": "function",
            "function": {
                "name": "get_skill_guidelines",
                "description": (
                    "The official knowledge base about Open WebUI Workspace "
                    "Skills: what a skill is, how it differs from a "
                    "model/agent, how to create/structure/activate one, the "
                    "SKILL.md format (frontmatter: name, description), the "
                    "recommended body sections (purpose, procedure with "
                    "reasons, output template, worked examples, edge cases, "
                    "quality checklist) and the style rules.\n\n"
                    "CALL THIS TOOL whenever the user mentions skills — "
                    "e.g. \"what is a skill\", \"how do I create a skill\", "
                    "\"skill vs agent\", \"skill structure\", \"SKILL.md\", "
                    "\"skill examples\", \"best practices\", \"improve my "
                    "skill\" — even if they are only asking a question and "
                    "not creating anything yet. Answer FROM the guidelines "
                    "instead of improvising. Also ALWAYS call it before "
                    "drafting or updating any skill (create_skill enforces "
                    "this with a quality gate)."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                },
            },
        }
