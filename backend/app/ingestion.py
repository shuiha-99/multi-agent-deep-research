from __future__ import annotations
import hashlib
import io
from pathlib import Path
import pandas as pd
from pypdf import PdfReader
from app.config import PROJECT_ROOT


def ingest_dataset(repo, name: str, content: bytes, metadata=None):
    if Path(name).suffix.lower() != ".csv":
        raise ValueError("经营数据请上传 CSV 文件")
    if not content:
        raise ValueError("文件为空")
    last_error = None
    for encoding in ["utf-8-sig", "gb18030"]:
        try:
            frame = pd.read_csv(io.BytesIO(content), encoding=encoding)
            break
        except (UnicodeDecodeError, pd.errors.ParserError) as exc:
            last_error = exc
    else:
        raise ValueError("CSV 无法解析，请使用 UTF-8 编码并检查分隔符") from last_error
    if frame.empty or len(frame.columns) == 0:
        raise ValueError("CSV 没有可分析的数据行")
    if len(frame) > 100_000 or len(frame.columns) > 100:
        raise ValueError("演示版最多支持 10 万行、100 列的数据集")
    frame.columns = [str(c).strip() for c in frame.columns]
    if len(set(frame.columns)) != len(frame.columns):
        raise ValueError("CSV 存在重复列名")
    folder = repo.data_dir / "datasets"
    folder.mkdir(exist_ok=True)
    digest = hashlib.sha256(content).hexdigest()
    path = folder / f"{digest[:20]}.csv"
    frame.to_csv(path, index=False, encoding="utf-8-sig")
    meta = {"sha256": digest, "missing_values": {str(k): int(v) for k, v in frame.isna().sum().items()}, **(metadata or {})}
    return repo.add_dataset(name, str(path), frame.columns.tolist(), len(frame), meta)


def ingest_document(repo, name: str, content: bytes, metadata=None):
    extension = Path(name).suffix.lower()
    if extension not in {".txt", ".md", ".pdf"}:
        raise ValueError("知识资料支持 TXT、Markdown 和可提取文本的 PDF")
    meta = {"sha256": hashlib.sha256(content).hexdigest(), **(metadata or {})}
    if extension == ".pdf":
        reader = PdfReader(io.BytesIO(content))
        if reader.is_encrypted:
            raise ValueError("请先解除 PDF 密码后上传")
        if len(reader.pages) > 300:
            raise ValueError("演示版最多支持 300 页 PDF")
        texts = []
        for i, page in enumerate(reader.pages):
            texts.append(f"\n[第 {i+1} 页]\n{page.extract_text() or ''}")
        text = "\n".join(texts)
        meta["page_count"] = len(reader.pages)
        if not any((p.extract_text() or "").strip() for p in reader.pages):
            raise ValueError("PDF 未提取到正文，扫描件需先进行 OCR")
    else:
        try:
            text = content.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = content.decode("gb18030")
    if not text.strip():
        raise ValueError("资料正文为空")
    if len(text) > 1_500_000:
        raise ValueError("资料正文过长，请拆分后上传")
    return repo.add_document(name, text, meta)


def load_samples(repo):
    folder = PROJECT_ROOT / "samples"
    docs = []
    datasets = []
    for path in sorted((folder / "documents").glob("*.md")):
        existing = next((d for d in repo.list_documents() if d["metadata"].get("sample_file") == path.name), None)
        docs.append(existing or ingest_document(repo, path.name, path.read_bytes(), {"sample_file": path.name, "simulated": True, "is_demo": True, "is_synthetic": True}))
    for path in sorted(folder.glob("*.csv")):
        existing = next((d for d in repo.list_datasets() if d["metadata"].get("sample_file") == path.name), None)
        datasets.append(existing or ingest_dataset(repo, path.name, path.read_bytes(), {"sample_file": path.name, "simulated": True, "is_demo": True, "is_synthetic": True}))
    if not docs or not datasets:
        raise ValueError("示例资料未安装完整，请检查 samples 目录")
    return {"documents": docs, "datasets": datasets}
