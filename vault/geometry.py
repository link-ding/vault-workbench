"""Dependency-free binary STL bounds and a sampled preview (never changes STL)."""
from functools import lru_cache
import math
import struct

from .catalog import ValidationError


@lru_cache(maxsize=24)
def preview(path_string, size, mtime_ns):
    with open(path_string, "rb") as stream:
        header = stream.read(84)
        if len(header) != 84:
            raise ValidationError("STL 文件太短。")
        count = struct.unpack_from("<I", header, 80)[0]
        if 84 + count * 50 != size:
            raise ValidationError("当前预览支持二进制 STL；文件仍可正常导出。")
        stride = 1 if count <= 120000 else max(1, count // 24000)
        triangles = []
        bounds_min = [math.inf] * 3
        bounds_max = [-math.inf] * 3
        offset = 0
        while block := stream.read(50 * 12000):
            for i, values in enumerate(struct.iter_unpack("<12fH", block)):
                points = values[3:12]
                if not all(math.isfinite(x) for x in points):
                    raise ValidationError("STL 包含无效坐标。")
                for axis in range(3):
                    bounds_min[axis] = min(bounds_min[axis], points[axis], points[axis + 3], points[axis + 6])
                    bounds_max[axis] = max(bounds_max[axis], points[axis], points[axis + 3], points[axis + 6])
                if (offset + i) % stride == 0:
                    triangles.append([round(float(x), 3) for x in points])
            offset += len(block) // 50
    return {"triangles": triangles, "faces": count, "min": bounds_min, "max": bounds_max,
            "dimensions": [round(b - a, 2) for a, b in zip(bounds_min, bounds_max)],
            "note": "STL 坐标轴包围盒尺寸（mm），不是装配尺寸或打印空间；预览已抽样。"}
