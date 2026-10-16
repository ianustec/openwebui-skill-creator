"""Quality-gate tests; run with python -m unittest discover -s tests -v."""
import importlib.util
from pathlib import Path
import itertools
import unicodedata
import unittest
from unittest.mock import AsyncMock, patch


SPEC = importlib.util.spec_from_file_location(
    "skill_creator", Path(__file__).resolve().parents[1] / "skill_creator.py"
)
assert SPEC is not None and SPEC.loader is not None
skill_creator = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(skill_creator)
analyze = skill_creator._analyze_skill_body

FRENCH = """## Objectif / Quand l'utiliser
Rédiger une fiche de visite quand le client le demande.
## Procédure / Étapes
1. Lire les données pour identifier les faits.
## Format de sortie
Rendre une fiche structurée.
## Exemples
Entrée : visite du 15 octobre. Sortie : fiche datée.
## Cas limites
Si rien n'est trouvé, demander une précision.
"""


class QualityGateTests(unittest.TestCase):
    def test_french_complete(self):
        self.assertEqual(analyze(FRENCH), [])

    def test_english_complete(self):
        self.assertEqual(analyze(
            "## Purpose\nWrite a report.\n## Procedure\n1. Read the data.\n"
            "## Output format\nA report.\n## Examples\nInput: data. Output: report.\n"
            "## Edge cases\nAsk if details are missing."
        ), [])

    def test_italian_complete(self):
        self.assertEqual(analyze(
            "## Quando usare\nPreparare una scheda.\n## Procedura\n1. Leggere i dati.\n"
            "## Struttura\nScheda.\n## Esempi\nDomanda e risposta.\n"
            "## Non trovato\nChiedere chiarimenti."
        ), [])

    def test_french_without_accents(self):
        plain = "".join(c for c in unicodedata.normalize("NFKD", FRENCH)
                        if not unicodedata.combining(c))
        self.assertEqual(analyze(plain), [])

    def test_french_uppercase(self):
        self.assertEqual(analyze(FRENCH.upper()), [])

    def test_french_decomposed_accents(self):
        self.assertEqual(analyze(unicodedata.normalize("NFD", FRENCH)), [])

    def test_empty_body_preserves_no_warnings(self):
        for body in ("", " \n\t", None):
            with self.subTest(body=body):
                self.assertEqual(analyze(body), [])

    def test_no_sections_preserves_all_six_warnings(self):
        # Five missing sections PLUS the separate worked-example warning.
        self.assertEqual(analyze("Bonjour."), [
            "no clear 'purpose' section", "no clear 'procedure' section",
            "no clear 'output format' section", "no clear 'examples' section",
            "no clear 'edge cases' section", "no worked example (input → output)",
        ])

    def test_original_french_reproduction_has_five_warnings(self):
        # 'Format' already matched upstream, so the measured failure has five.
        self.assertEqual(analyze("Format de sortie"), [
            "no clear 'purpose' section", "no clear 'procedure' section",
            "no clear 'examples' section", "no clear 'edge cases' section",
            "no worked example (input → output)",
        ])

    def test_input_output_alone_is_not_a_worked_example(self):
        self.assertEqual(analyze("Purpose procedure input: output: error"), [
            "no worked example (input → output)",
        ])

    def test_french_worked_example_singular_and_plural(self):
        for word in ("Exemple", "Exemples", "EXEMPLES"):
            with self.subTest(word=word):
                self.assertEqual(analyze("Purpose procedure output error " + word), [])

    def test_each_french_section_hint(self):
        groups = (
            ("purpose", ("objectif", "finalité", "quand l'utiliser", "quand l’utiliser",
                         "quand utiliser", "déclencheur", "déclencheurs")),
            ("procedure", ("procédure", "étape", "étapes", "méthode", "déroulé", "processus")),
            ("output format", ("format de sortie", "sortie attendue", "sorties attendues",
                               "modèle", "modèles", "gabarit", "gabarits")),
            ("examples", ("exemple", "exemples", "exemple d'entrée/sortie")),
            ("edge cases", ("cas limite", "cas limites", "erreur", "erreurs", "hors périmètre",
                            "si rien n'est trouvé", "échec", "cas particulier", "cas particuliers")),
        )
        for label, hints in groups:
            for hint in hints:
                with self.subTest(label=label, hint=hint):
                    self.assertNotIn(f"no clear '{label}' section", analyze(hint.upper()))

    def test_english_but_does_not_count_as_french_purpose(self):
        # "but" is a common English word (and a substring of button/attribute):
        # it must not satisfy the purpose check.
        body = "Steps: click the button, but check the attribute. Output: table. Example. Error."
        self.assertIn("no clear 'purpose' section", analyze(body))

    def test_unstructured_logs_still_need_output_format(self):
        body = ("Purpose: review logs. Procedure: read logs. "
                "Example: detect an error in unstructured logs.")
        self.assertEqual(analyze(body), ["no clear 'output format' section"])

    def test_modeled_failure_still_needs_output_format(self):
        body = ("Purpose: inspect a system. Procedure: inspect it. "
                "Example: a modeled failure.")
        self.assertEqual(analyze(body), ["no clear 'output format' section"])

    def test_english_structure_in_prose_is_not_an_output_hint(self):
        body = ("Purpose: review logs. Procedure: inspect the structure of logs. "
                "Example: detect an error.")
        self.assertEqual(analyze(body), ["no clear 'output format' section"])

    def test_english_sortie_is_not_an_output_hint(self):
        # "sortie" is also an English noun (a flight or sally).
        body = ("Purpose: review flight logs. Procedure: read every sortie record. "
                "Example: an engine failure.")
        self.assertEqual(analyze(body), ["no clear 'output format' section"])

    def test_english_sortie_and_entree_labels_are_not_example_hints(self):
        # Spaced colons must not turn English "entree"/"sortie" into examples.
        for body in ("Purpose procedure output error. Entree : soup.",
                     "Purpose procedure output error. Sortie : night patrol."):
            with self.subTest(body=body):
                self.assertEqual(analyze(body), [
                    "no clear 'examples' section", "no worked example (input → output)",
                ])

    def test_new_french_hints_do_not_match_inside_words(self):
        for label, hint in (("purpose", "objectif"), ("output format", "modele"),
                            ("output format", "gabarit"), ("examples", "exemple"),
                            ("edge cases", "erreur")):
            for body in ("x" + hint, hint + "x", "x" + hint + "y"):
                with self.subTest(body=body):
                    self.assertIn(f"no clear '{label}' section", analyze(body))

    def test_french_template_singular_plural_and_punctuation(self):
        for hint in ("modèle", "modèles", "modele", "modeles", "gabarit", "gabarits"):
            for body in (hint, "## " + hint.upper(), "**" + hint + "**:"):
                with self.subTest(body=body):
                    self.assertNotIn("no clear 'output format' section", analyze(body))

    def test_legacy_english_italian_subsets(self):
        # Golden warning lists for accepted and rejected EN/IT section subsets.
        labels = ("purpose", "procedure", "output format", "examples", "edge cases")
        for hints in (("purpose", "procedure", "output", "example", "error"),
                      ("scopo", "procedura", "struttura", "esempio", "non trovato")):
            for present in itertools.product((False, True), repeat=5):
                body = "\n".join(h for h, yes in zip(hints, present) if yes) or "Hello."
                expected = [f"no clear '{label}' section"
                            for label, yes in zip(labels, present) if not yes]
                if not present[3]:
                    expected.append("no worked example (input → output)")
                with self.subTest(hints=hints, present=present):
                    self.assertEqual(analyze(body), expected)
                    self.assertEqual(analyze(body.upper()), expected)

    def test_all_legacy_hints_remain_recognized(self):
        groups = (
            ("purpose", ("purpose", "when to use", "scopo", "quando usar")),
            ("procedure", ("procedure", "step", "workflow", "process", "procedura")),
            ("output format", ("output", "template", "format", "struttura")),
            ("examples", ("example", "esempio", "esempi", "input:", "output:")),
            ("edge cases", ("edge case", "error", "fallback", "not found",
                            "non trov", "fuori scope", "out of scope", "failure")),
        )
        for label, hints in groups:
            for hint in hints:
                with self.subTest(label=label, hint=hint):
                    self.assertNotIn(f"no clear '{label}' section", analyze(hint))

    def test_missing_french_example_still_warns(self):
        self.assertEqual(analyze("Objectif procédure gabarit cas limites"), [
            "no clear 'examples' section", "no worked example (input → output)",
        ])

    def test_normalizer_handles_case_and_accents(self):
        normalize = skill_creator._normalize_guideline_text
        self.assertEqual(normalize("PROCÉDURE ÉTAPES MODÈLE ÉCHEC"),
                         "procedure etapes modele echec")
        self.assertEqual(normalize("É"), normalize("E\u0301"))


class CreateSkillQualityGateTests(unittest.IsolatedAsyncioTestCase):
    async def test_rejected_english_drafts_do_not_write(self):
        for body in (
            "Purpose: review logs. Procedure: read logs. Example: detect an error in unstructured logs.",
            "Purpose: inspect a system. Procedure: inspect it. Example: a modeled failure.",
            "Purpose: review flight logs. Procedure: read every sortie record. "
            "Example: an engine failure.",
        ):
            with self.subTest(body=body):
                tool = skill_creator.Tools()
                content = "---\nname: inspect-logs\ndescription: Review logs on request.\n---\n" + body
                api = AsyncMock(return_value=(200, {"id": "inspect-logs"}))
                with patch.object(skill_creator, "_skills_api", api):
                    result = await tool.create_skill(content)
                self.assertIn("the skill was NOT saved", result)
                self.assertIn("no clear 'output format' section", result)
                api.assert_not_awaited()

    async def test_french_draft_is_saved_without_force(self):
        tool = skill_creator.Tools()
        content = "---\nname: fiche-visite\ndescription: Rédiger une fiche lors des visites.\n---\n" + FRENCH
        api = AsyncMock(return_value=(200, {"id": "fiche-visite"}))
        with patch.object(skill_creator, "_skills_api", api):
            result = await tool.create_skill(content)
        self.assertIn("Skill created:", result)
        self.assertNotIn("missing recommended sections", result)
        api.assert_awaited_once()
        assert api.await_args is not None
        self.assertEqual(api.await_args.kwargs["method"], "POST")
        self.assertEqual(api.await_args.kwargs["path"], "/create")
        self.assertEqual(api.await_args.kwargs["json_body"]["content"], FRENCH.strip())
        self.assertEqual(api.await_args.kwargs["json_body"]["access_grants"], [])


if __name__ == "__main__":
    unittest.main()
