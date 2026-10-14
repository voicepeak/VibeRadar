"""Download pinned source into the trial workspace; reject archive traversal and links."""
import io
import tarfile
import httpx


def download_source(recipe, directory):
    repo = recipe.repo.removeprefix("https://github.com/").removesuffix(".git").strip("/")
    if len(repo.split("/")) != 2:
        raise ValueError("Invalid GitHub repository")
    with httpx.Client(timeout=60, follow_redirects=False, trust_env=False) as client:
        with client.stream("GET", f"https://codeload.github.com/{repo}/tar.gz/{recipe.revision}") as response:
            response.raise_for_status()
            data = bytearray()
            for chunk in response.iter_bytes():
                data.extend(chunk)
                if len(data) > 50_000_000:
                    raise ValueError("Source archive exceeds 50MB")
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
        total = 0
        for member in archive:
            parts = member.name.split("/")[1:]
            if not parts or member.isdir():
                continue
            if not member.isfile() or ".." in parts or any("\\" in p or ":" in p for p in parts):
                raise ValueError("Unsafe source archive member")
            target = directory.joinpath(*parts).resolve()
            if not target.is_relative_to(directory.resolve()):
                raise ValueError("Source archive escapes workspace")
            total += member.size
            if total > 200_000_000:
                raise ValueError("Extracted source exceeds 200MB")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(archive.extractfile(member).read())
            target.chmod(0o777 if member.mode & 0o111 else 0o666)
