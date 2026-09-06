from pathlib import Path


def test_all_skills_have_frontmatter_and_safety_language():
    for path in Path("skills").glob("*/SKILL.md"):
        text = path.read_text()
        assert text.startswith("---\n")
        assert "candidate ID" in text or "candidate IDs" in text
        assert "Trash" in text or "Recycle Bin" in text


def test_skill_files_have_real_cli_commands():
    for path in Path("skills").glob("*/SKILL.md"):
        text = path.read_text()
        assert "freeup-space" in text
        assert "candidate-id" in text or "candidate IDs" in text


def test_docs_cover_permission_and_recovery_guidance():
    readme = Path("README.md").read_text()
    security = Path("SECURITY.md").read_text()
    permissions = Path("docs/codex-permissions.md").read_text()
    sample = Path("examples/sample-report.md").read_text()

    assert "Full Disk Access" in readme
    assert "Windows" in readme
    assert "Trash" in readme and "Recycle Bin" in readme
    assert "exact candidate IDs" in readme
    assert "narrowest sufficient" in security
    assert "Freeup Space" in permissions
    assert "DUP-001" in sample and "OLD-004" in sample
