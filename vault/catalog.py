"""Index actual files without renaming, modifying, or guessing paths."""
from dataclasses import dataclass, asdict
from pathlib import Path
import hashlib
import re


DEFAULT_ROOT = r"D:\261020_VAULT_USSA\261020_VAULT_USSA"
KINDS = {"01. DRAWERS": "drawer", "02. SUSTAINERS": "sustainer", "03. MODIFICATIONS": "mod"}
KIND_LABELS = {"drawer": "抽屉", "sustainer": "翻盖箱", "mod": "改装配件"}
DOCS = [
    ("01_Instructions Manual.pdf", "总说明与零件命名", 20),
    ("02_Drawer Assembly.pdf", "抽屉装配", 8),
    ("03_Sustainers Assembly.pdf", "翻盖箱装配", 7),
    ("04_Modifications.pdf", "改装配件装配", 17),
    ("05_Mounting Instructions.pdf", "墙挂与桌下安装", 12),
]


class ValidationError(ValueError):
    pass


def normalized(value):
    # Preserve superscript ²; NFKC would silently change special model families.
    return re.sub(r"[\s_'’()\-]+", "", value.lower())


def chinese_title(name, rel):
    n = name.lower()
    pairs = [
        ("1 set of pins", "翻盖锁销（一整套）"), ("connector for split roof", "分体顶盖连接件"),
        ("runner stopper", "滑轨限位块"), ("runner guides", "外导轨（左右一对）"),
        ("runner slides", "内滑轨（左右一对）"), ("hinge top", "上铰链"),
        ("hinge", "下铰链（组合文件）"), ("rods", "铰链轴（一对）"),
        ("lock outer", "翻盖锁外扣"), ("lock inner", "翻盖锁内扣"), ("lock holder", "翻盖锁座"),
        ("hidden key part 2", "隐藏式旋钮第二件"), ("hidden key", "隐藏式锁定旋钮"),
        ("key cap", "旋钮盖"), ("key (", "锁定旋钮"), ("facade cap", "面板螺丝孔盖"),
        ("springs", "锁定弹簧（一对）"), ("latch", "堆叠连接扣"),
        ("heawyweight roof handle", "承重顶盖把手"), ("heawyweight roof insert", "承重顶盖插片"),
        ("top handle", "顶部提手"), ("label", "标签片"), ("rails", "锁定齿条（一组）"),
        ("under table", "桌下安装顶盖"), ("grid roof", "Gridfinity 网格顶盖"),
        ("table roof", "平面顶盖"), ("bowl roof", "托盘顶盖"), ("heawyweight roof", "承重顶盖"),
        ("roof", "翻盖箱顶盖"), ("baseplate", "Gridfinity 内部底板"),
        ("module", "外壳 / 箱体"), ("box", "内抽盒"), ("facade", "前面板"),
        ("seal divider", "封闭分隔片"), ("width divider", "横向分隔片"),
        ("depth divider", "纵向分隔片"), ("divider", "分隔片"),
        ("backbone", "背梁"), ("pull-out handle lock", "拉杆锁座"),
        ("handle lock knob", "拉杆锁旋钮"), ("pull-out handle", "拉杆把手"),
        ("stationary handle", "固定把手"), ("wall mount second", "墙挂连接片"),
        ("wall mount", "墙挂板 / 定位模板"), ("feet", "底脚"), ("base connector", "分体底座连接件"),
        ("base", "底座"), ("tyre", "轮胎（TPU）"), ("bushing", "轮轴衬套"),
        ("cover", "轮盖"), ("disk", "轮毂"), ("jig", "衬套装配治具"),
        ("accessory", "双列外壳配件"), ("connector", "连接件"),
    ]
    if "Grid Dividers/" in rel:
        return "抽屉网格分隔条 · " + name.removesuffix(".stl")
    if "/Facades/" in rel and "facade" not in n:
        return "前面板 · " + name.removesuffix(".stl")
    if "Pegboard Attachment/" in rel:
        return "洞洞板连接件 · " + name.removesuffix(".stl")
    if "/Manual Supports/" in rel:
        return "辅助打印支撑 · " + name.removesuffix(".stl")
    for needle, title in pairs:
        if needle in n:
            return title
    return name.removesuffix(".stl")


def file_group(rel, name):
    lower = rel.lower()
    if any(x in lower for x in ["/modules/", "/boxes/", "/facades/"]):
        return "主体"
    if any(x in lower for x in ["/runners/", "/hinges/", "/locks/", "/rods/", "/geared rails/"]):
        return "机构"
    if "tyre" in lower:
        return "TPU"
    return "配件"


@dataclass(frozen=True)
class ModelFile:
    id: str
    relative: str
    name: str
    title: str
    kind: str
    family: str
    model: str
    size: int
    group: str
    supports: bool
    material: str

    def public(self):
        return asdict(self)


class Catalog:
    def __init__(self, root):
        self.root = Path(root).expanduser().resolve()
        if not self.root.is_dir():
            raise ValidationError("模型库文件夹不存在，请检查路径。")
        self.files = []
        self.by_id = {}
        self.models = {}
        for path in sorted(self.root.rglob("*")):
            if not path.is_file() or path.suffix.lower() != ".stl":
                continue
            if not path.resolve().is_relative_to(self.root):
                continue
            parts = path.relative_to(self.root).parts
            kind = KINDS.get(parts[0])
            if not kind:
                continue
            rel = "/".join(parts)
            family = ""
            if len(parts) > 1:
                match = re.fullmatch(r"\d+\. (S|M|L²|L|XL²|XL) Families", parts[1])
                if match:
                    family = match[1]
            model = next((part for part in parts[2:-1] if re.fullmatch(r"(?:XL|L|M|S)[3-7]²?", part)), "")
            entry = ModelFile(
                hashlib.sha256(rel.encode("utf-8")).hexdigest()[:16], rel, path.name,
                chinese_title(path.name, rel), kind, family, model, path.stat().st_size,
                file_group(rel, path.name), "turn on supports" in path.name.lower(),
                "TPU" if "tpu" in path.name.lower() else "PLA / PETG",
            )
            self.files.append(entry)
            self.by_id[entry.id] = entry
            if model and "module" in path.name.lower():
                height = re.search(r"\b([124])f\b", path.name, re.I)
                if height:
                    key = f"{kind}:{model}"
                    obj = self.models.setdefault(key, {"key": key, "kind": kind, "family": family,
                        "model": model, "depth": int(re.search(r"[3-7]", model)[0]), "heights": [],
                        "verified": "²" not in model})
                    obj["heights"] = sorted(set(obj["heights"]) | {int(height[1])})
        if not self.files:
            raise ValidationError("未找到 VAULT STL 文件。请选择包含 01. DRAWERS 等目录的根文件夹。")

    def resolve(self, file_id):
        entry = self.by_id.get(file_id)
        if not entry:
            raise ValidationError("文件不在当前模型库索引中，请重新扫描。")
        path = (self.root / entry.relative).resolve()
        if not path.is_relative_to(self.root) or not path.is_file():
            raise ValidationError("源文件不存在或已移出模型库：" + entry.relative)
        if path.stat().st_size != entry.size:
            raise ValidationError("源文件已变化，请重新扫描：" + entry.name)
        return path

    def match(self, name, *, kind=None, model=None, family=None, folder=None):
        found = [f for f in self.files if normalized(f.name) == normalized(name)
                 and (kind is None or f.kind == kind) and (model is None or f.model == model)
                 and (family is None or f.family == family)
                 and (folder is None or folder.lower() in f.relative.lower())]
        if len(found) != 1:
            why = "缺少文件" if not found else "发现多个同名候选，需核对"
            raise ValidationError(f"{why}：{name}。请检查模型库。")
        return found[0]

    def public(self):
        counts = {k: sum(f.kind == k for f in self.files) for k in KIND_LABELS}
        return {"root": str(self.root), "count": len(self.files), "bytes": sum(f.size for f in self.files),
                "counts": counts, "models": list(self.models.values()),
                "files": [f.public() for f in self.files],
                "documents": [{"id": str(i + 1), "title": title, "name": name, "pages": pages,
                               "exists": (self.root / name).is_file()} for i, (name, title, pages) in enumerate(DOCS)]}
