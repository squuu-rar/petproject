import pytest

# Dummy classes to simulate mutagen structures
class DummyAPIC:
    def __init__(self, data):
        self.data = data

class DummyPicture:
    def __init__(self, data):
        self.data = data

class DummyInfo:
    def __init__(self, length):
        self.length = length

class DummyFile:
    def __init__(self, tags=None, pictures=None, info=None):
        self.tags = tags or {}
        self.pictures = pictures
        self.info = info or DummyInfo(300.0)

@pytest.fixture
def scanner_module(tmp_path):
    import scanner
    # Patch covers directory to a temp location
    scanner.COVERS_DIR = tmp_path / "static" / "covers"
    return scanner

def test_extract_cover_art_mp3(scanner_module, tmp_path, monkeypatch):
    file_path = tmp_path / "song.mp3"
    file_path.write_bytes(b"")
    cover_bytes = b"fakejpegdata"
    tags = {"title": ["Test Title"], "APIC": DummyAPIC(cover_bytes)}
    monkeypatch.setattr(scanner_module.mutagen, "File", lambda name, easy=True: DummyFile(tags=tags, info=DummyInfo(180.0)))
    scanner_module.scan(tmp_path)
    covers_dir = scanner_module.COVERS_DIR
    assert covers_dir.exists()
    jpg_files = list(covers_dir.glob("*.jpg"))
    assert len(jpg_files) == 1
    with open(jpg_files[0], "rb") as f:
        data = f.read()
    assert data == cover_bytes

def test_extract_cover_art_flac(scanner_module, tmp_path, monkeypatch):
    file_path = tmp_path / "song.flac"
    file_path.write_bytes(b"")
    cover_bytes = b"alsofakejpeg"
    pictures = [DummyPicture(cover_bytes)]
    tags = {"title": ["Flac Title"]}
    monkeypatch.setattr(scanner_module.mutagen, "File", lambda name, easy=True: DummyFile(tags=tags, pictures=pictures, info=DummyInfo(200.0)))
    scanner_module.scan(tmp_path)
    covers_dir = scanner_module.COVERS_DIR
    assert covers_dir.exists()
    jpg_files = list(covers_dir.glob("*.jpg"))
    assert len(jpg_files) == 1
    with open(jpg_files[0], "rb") as f:
        data = f.read()
    assert data == cover_bytes

def test_no_cover_art(scanner_module, tmp_path, monkeypatch):
    file_path = tmp_path / "track.ogg"
    file_path.write_bytes(b"")
    tags = {"title": ["OGG Title"]}
    monkeypatch.setattr(scanner_module.mutagen, "File", lambda name, easy=True: DummyFile(tags=tags, info=DummyInfo(150.0)))
    scanner_module.scan(tmp_path)
    covers_dir = scanner_module.COVERS_DIR
    jpg_files = list(covers_dir.glob("*.jpg")) if covers_dir.exists() else []
    assert len(jpg_files) == 0
