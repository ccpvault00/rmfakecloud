"""Dump what the viewer should draw for a v6 .rm file, using rmscene as the reference."""
import json, sys, logging
logging.disable(logging.CRITICAL)
from rmscene import read_tree, scene_items as si

def dump(path):
    tree = read_tree(open(path, 'rb'))
    lines, hls = [], []
    def walk(g, dx):
        if g.visible is not None and not g.visible.value:
            return
        if g.anchor_origin_x is not None:
            dx += g.anchor_origin_x.value
        for ch in g.children.values():
            if isinstance(ch, si.Group):
                walk(ch, dx)
            elif isinstance(ch, si.Line):
                lines.append({'tool': int(ch.tool), 'color': int(ch.color), 'thickness': ch.thickness_scale,
                              'points': [[round(p.x + dx, 3), round(p.y, 3), int(p.speed), int(p.width), int(p.direction), int(p.pressure)] for p in ch.points]})
            elif isinstance(ch, si.GlyphRange):
                hls.append({'color': int(ch.color), 'text': ch.text,
                            'rects': [[round(r.x + dx, 3), round(r.y, 3), round(r.w, 3), round(r.h, 3)] for r in ch.rectangles]})
    walk(tree.root, 0.0)
    return {'lines': lines, 'highlights': hls, 'hasText': tree.root_text is not None}

if __name__ == '__main__':
    json.dump(dump(sys.argv[1]), sys.stdout)
