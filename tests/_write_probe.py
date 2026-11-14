"""파일 쓰기 도중을 재현하는 테스트 도구.

`pathlib.Path.write_text`를 감싸 지정 디렉터리 안의 쓰기를 절반에서 멈추고 `on_half`를
부른다. 비원자적 쓰기(대상 파일을 직접 여는 방식)라면 그 순간 대상 파일이 반쯤 쓰인
상태로 드러나고, 원자적 쓰기(임시 파일 → os.replace)라면 임시 파일만 반쯤 쓰여 있다.
"""
import json
import pathlib


def split_writes(monkeypatch, directory: pathlib.Path, on_half) -> list:
    """directory 안 파일 쓰기를 둘로 쪼개고 사이에 on_half(path)를 부른다.

    반환 리스트에 실제로 쪼개진 쓰기의 경로가 쌓인다 — 테스트가 이 경로를 실제로
    탔는지 단언해 쓰기 방식이 바뀌었을 때 헛통과하지 않게 한다.
    """
    real = pathlib.Path.write_text
    directory = pathlib.Path(directory)
    hits = []

    def write_text(self, data, encoding=None, errors=None, newline=None):
        if self.parent != directory:
            return real(self, data, encoding=encoding, errors=errors, newline=newline)
        hits.append(self)
        half = len(data) // 2
        with self.open('w', encoding=encoding, errors=errors, newline=newline) as f:
            f.write(data[:half])
            f.flush()
            on_half(self)
            f.write(data[half:])
        return len(data)

    monkeypatch.setattr(pathlib.Path, 'write_text', write_text)
    return hits


def read_json_or_partial(path: pathlib.Path):
    """대상 파일을 읽어 JSON으로 돌려준다. 반쯤 쓰여 파싱이 안 되면 'PARTIAL'."""
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except ValueError:
        return 'PARTIAL'
