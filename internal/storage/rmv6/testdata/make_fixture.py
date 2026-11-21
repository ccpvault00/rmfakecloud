"""Write synthetic v6 fixtures with rmscene's writer (no real user data)."""
import sys, io, uuid, pathlib
from rmscene import scene_stream as ss, scene_items as si
from rmscene.crdt_sequence import CrdtSequenceItem
from rmscene.tagged_block_common import CrdtId, LwwValue
out = pathlib.Path(sys.argv[1])
def pt(x, y, w=12, p=200): return si.Point(x=x, y=y, speed=5, direction=10, width=w, pressure=p)
def line(tool, color, pts): return si.Line(color=color, tool=tool, points=pts, thickness_scale=2.0, starting_length=0.0)
def item(i, left, right, value, deleted=0): return CrdtSequenceItem(item_id=CrdtId(1, i), left_id=left, right_id=right, deleted_length=deleted, value=value)
Z = CrdtId(0, 0)
L1, L2, L3 = CrdtId(0, 11), CrdtId(0, 21), CrdtId(0, 31)
blocks = [
    ss.AuthorIdsBlock(author_uuids={1: uuid.UUID(int=1)}),
    ss.MigrationInfoBlock(migration_id=CrdtId(1, 1), is_device=True),
    ss.PageInfoBlock(loads_count=1, merges_count=0, text_chars_count=0, text_lines_count=0),
    ss.SceneTreeBlock(tree_id=L1, node_id=Z, is_update=True, parent_id=CrdtId(0, 1)),
    ss.SceneTreeBlock(tree_id=L2, node_id=Z, is_update=True, parent_id=CrdtId(0, 1)),
    ss.SceneTreeBlock(tree_id=L3, node_id=Z, is_update=True, parent_id=CrdtId(0, 1)),
    ss.TreeNodeBlock(si.Group(node_id=CrdtId(0, 1))),
    ss.TreeNodeBlock(si.Group(node_id=L1, label=LwwValue(CrdtId(0, 12), "Layer 1"))),
    ss.TreeNodeBlock(si.Group(node_id=L2, label=LwwValue(CrdtId(0, 22), "hidden"), visible=LwwValue(CrdtId(0, 23), False))),
    ss.TreeNodeBlock(si.Group(node_id=L3, label=LwwValue(CrdtId(0, 32), "anchored"),
        anchor_id=LwwValue(CrdtId(0, 33), CrdtId(0, 281474976710654)), anchor_type=LwwValue(CrdtId(0, 34), 2),
        anchor_threshold=LwwValue(CrdtId(0, 35), 1.0), anchor_origin_x=LwwValue(CrdtId(0, 36), -100.0))),
    # layers in the root, inserted out of id order to exercise the left/right ordering
    ss.SceneGroupItemBlock(parent_id=CrdtId(0, 1), item=item(13, Z, Z, L1)),
    ss.SceneGroupItemBlock(parent_id=CrdtId(0, 1), item=item(14, CrdtId(1, 13), Z, L2)),
    ss.SceneGroupItemBlock(parent_id=CrdtId(0, 1), item=item(15, CrdtId(1, 14), Z, L3)),
    # layer 1: line B is linked to the left of line A although its id is larger
    ss.SceneLineItemBlock(parent_id=L1, item=item(101, CrdtId(1, 102), Z, line(si.Pen.BALLPOINT_2, si.PenColor.BLUE, [pt(-10, 20), pt(30, 40.5), pt(55.25, 60)]))),
    ss.SceneLineItemBlock(parent_id=L1, item=item(102, Z, CrdtId(1, 101), line(si.Pen.HIGHLIGHTER_2, si.PenColor.HIGHLIGHT, [pt(0, 0, 60, 100), pt(100, 0, 60, 100)]))),
    # deleted line keeps a place but draws nothing
    ss.SceneLineItemBlock(parent_id=L1, item=item(103, CrdtId(1, 101), Z, None, deleted=1)),
    ss.SceneGlyphItemBlock(parent_id=L1, item=item(104, CrdtId(1, 103), Z,
        si.GlyphRange(start=None, length=9, text="highlight", color=si.PenColor.YELLOW, rectangles=[si.Rectangle(-50.5, 100.0, 200.0, 34.25), si.Rectangle(-50.5, 140.0, 80.0, 34.25)]))),
    ss.SceneLineItemBlock(parent_id=L2, item=item(201, Z, Z, line(si.Pen.FINELINER_2, si.PenColor.RED, [pt(1, 1), pt(2, 2)]))),
    ss.SceneLineItemBlock(parent_id=L3, item=item(301, Z, Z, line(si.Pen.MARKER_2, si.PenColor.GREEN_2, [pt(5, 500), pt(6, 510)]))),
]
with open(out / "synthetic.rm", "wb") as f:
    ss.write_blocks(f, blocks, options={"version": "3.6"})
