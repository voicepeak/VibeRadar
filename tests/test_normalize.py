"""单元测试：URL 身份、共享域名、GitHub 映射（PRD M2/11.3）。"""

from __future__ import annotations

from viberadar.pipeline.normalize import extract_identity, github_owner_repo, strip_tracking


def test_strip_tracking_removes_utm_keeps_identity_query():
    url = "https://example.com/product?id=42&utm_source=x&utm_medium=y&fbclid=z"
    normalized = strip_tracking(url)
    assert "utm_source" not in normalized and "fbclid" not in normalized
    assert "id=42" in normalized


def test_strip_tracking_drops_fragment_and_trailing_slash():
    assert strip_tracking("https://example.com/a/#section") == "https://example.com/a"
    assert strip_tracking("https://example.com/") == "https://example.com/"


def test_github_identity_and_subpath():
    owner, repo, subpath = github_owner_repo("https://github.com/Owner/Repo/tree/main/docs")
    assert owner == "Owner" and repo == "Repo" and subpath == "/tree/main/docs"
    hints = extract_identity("https://github.com/Owner/Repo/tree/main/docs", "Repo")
    assert hints.canonical_key == "github:owner/repo"
    sub = extract_identity("https://github.com/owner/repo", "repo")
    assert sub.canonical_key == hints.canonical_key  # 子路径映射同一 repo


def test_github_reserved_paths_not_repos():
    assert github_owner_repo("https://github.com/topics/ai") is None
    assert github_owner_repo("https://github.com/orgs/acme") is None


def test_different_repos_not_merged():
    a = extract_identity("https://github.com/a/tool", "tool")
    b = extract_identity("https://github.com/b/tool", "tool")
    assert a.canonical_key != b.canonical_key


def test_shared_platform_same_host_different_paths_not_merged():
    a = extract_identity("https://myapp.vercel.app/", "myapp")
    b = extract_identity("https://otherapp.vercel.app/", "otherapp")
    assert a.canonical_key != b.canonical_key
    assert a.shared_platform and b.shared_platform
    assert any("共享平台" in w for w in a.warnings)


def test_same_url_normalization_merges():
    a = extract_identity("https://example.com/app?utm_source=home", "App")
    b = extract_identity("https://example.com/app/", "App")
    assert a.canonical_key == b.canonical_key


def test_repo_id_from_metadata_takes_priority_signal():
    hints = extract_identity(
        "https://github.com/owner/repo", "repo", metadata={"repo_id": 12345}
    )
    assert hints.repo_id == "12345"
