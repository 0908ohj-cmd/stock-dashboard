"""원자적 파일 쓰기.

대상 파일을 직접 열어 쓰면(write_text) 쓰는 동안 파일이 잘린 채로 보인다. 그 순간 다른
스레드가 읽으면 JSON 파싱이 실패하고, 실패를 '빈 파일'로 해석한 쪽이 새 항목만 넣어 다시
저장해 원본 전체가 사라졌다(#26, 300종목 스냅샷 → 0개, 섹터 캐시 1327개 → 2개).
같은 디렉터리의 임시 파일에 다 쓴 뒤 os.replace로 바꿔 끼우면, 읽는 쪽은 언제나 이전
파일이나 새 파일 중 하나를 온전히 본다.
"""
import os
import pathlib
import threading
import time

# Windows(실행.bat 런처)는 다른 스레드가 대상 파일을 읽고 있는 순간 os.replace가
# PermissionError를 낸다. 읽기는 수 ms면 끝나므로 잠깐 기다렸다 다시 시도한다.
_REPLACE_RETRIES = 5
_REPLACE_BACKOFF_SEC = 0.05


def _replace_with_retry(src: pathlib.Path, dst: pathlib.Path) -> None:
    for attempt in range(_REPLACE_RETRIES):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if attempt == _REPLACE_RETRIES - 1:
                raise
            time.sleep(_REPLACE_BACKOFF_SEC * (attempt + 1))


def atomic_write_text(path, text: str, encoding: str = 'utf-8') -> None:
    path = pathlib.Path(path)
    # 같은 디렉터리여야 os.replace가 원자적이다(같은 파일시스템). 프로세스·스레드별 이름이라
    # 동시에 쓰는 쪽끼리 임시 파일을 밟지 않는다. 점으로 시작 + .tmp — .gitignore가 거른다
    tmp = path.with_name(f'.{path.name}.{os.getpid()}.{threading.get_ident()}.tmp')
    try:
        tmp.write_text(text, encoding=encoding)
        _replace_with_retry(tmp, path)
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise
