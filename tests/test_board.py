"""Tests for the requirement board and its shape measurements, on trimesh primitives."""
import math

import numpy as np
import pytest
import trimesh
import trimesh.transformations as tf

from review3d import board as B


def box(extents, at=(0.0, 0.0, 0.0)):
    m = trimesh.creation.box(extents=extents)
    m.apply_translation(at)
    return m


# ------------------------------------------------------------------------------------ metrics
def test_mirror_symmetric_box_reads_zero():
    assert B.mirror_asymmetry(box((10, 6, 4)), [1, 0, 0]) == pytest.approx(0.0, abs=1e-9)


def test_mirror_bump_reads_its_height():
    # a 2 mm cube on the +x face: its mirror image stands 2 mm off the -x face
    m = trimesh.util.concatenate([box((10, 6, 4)), box((2, 2, 2), (6, 1, 0))])
    assert B.mirror_asymmetry(m, [1, 0, 0]) == pytest.approx(2.0, abs=1e-6)


def test_mirror_gap_matches_a_mirrored_pair():
    left, right = box((3, 2, 2), (-5, 0, 0)), box((3, 2, 2), (5, 0, 0))
    assert B.mirror_gap(left, right, [1, 0, 0]) == pytest.approx(0.0, abs=1e-9)
    longer = box((4, 2, 2), (5.5, 0, 0))
    assert B.mirror_gap(left, longer, [1, 0, 0]) == pytest.approx(1.0, abs=1e-6)


def test_sphericity_sphere_and_egg():
    s = trimesh.creation.icosphere(subdivisions=4, radius=10)
    assert B.sphericity(s) < 0.005
    egg = s.copy()
    egg.apply_scale([1.0, 1.0, 0.62])
    assert B.sphericity(egg) > 0.08


def test_roughness_box_and_sphere():
    assert B.roughness(box((10, 6, 4))) == pytest.approx(90.0)
    assert B.roughness(trimesh.creation.icosphere(subdivisions=3, radius=10)) < 8.0


def test_roughness_skip_leaves_out_a_sharp_feature():
    # a smooth sphere carrying a small box: the box's edges are sharp on purpose
    s = trimesh.creation.icosphere(subdivisions=3, radius=10)
    m = trimesh.util.concatenate([s, box((1, 1, 1), (10.4, 0, 0))])
    assert B.roughness(m, percentile=99.9) > 80.0
    assert B.roughness(m, skip=[((10.4, 0, 0), 1.5)], percentile=99.9) < 10.0


def test_relief_depth_finds_a_dent_and_ignores_a_plain_sphere():
    s = trimesh.creation.icosphere(subdivisions=4, radius=10)
    assert B.relief_depth(s) < 0.05
    v = s.vertices.copy()
    v[v[:, 0] > 9.5] *= 0.9                      # press a cap 1 mm in
    dented = trimesh.Trimesh(v, s.faces, process=False)
    assert B.relief_depth(dented) == pytest.approx(1.0, abs=0.05)
    assert B.relief_depth(dented, facing=[-1, 0, 0]) < 0.05


def test_gap_between_two_boxes():
    a = box((10, 10, 10))
    b = box((4, 4, 4), (5 + 2 + 0.3, 0, 0))
    assert B.gap(a, b) == pytest.approx(0.3, abs=1e-9)


def test_penetration_depth_and_no_gap_when_overlapping():
    a = box((10, 10, 10))
    b = box((4, 4, 4), (5 + 2 - 0.5, 0, 0))
    assert B.penetration(a, b) == pytest.approx(0.5, abs=1e-9)
    assert B.gap(a, b) == 0.0
    assert B.penetration(a, box((4, 4, 4), (10, 0, 0))) == 0.0


def test_inside_and_distance_helpers():
    a = box((2, 2, 2))
    assert B.inside(a, [[0, 0, 0], [3, 0, 0]]).tolist() == [True, False]
    assert B.distance_to_surface(a, [[3, 0, 0], [0, 0, 0]]) == pytest.approx([2.0, 1.0])


def test_tip_ratio_stands_and_tips():
    base = box((10, 10, 1), (0, 0, 0.5))
    assert B.tip_ratio(base) == pytest.approx(0.0, abs=1e-9)
    # a 720 mm3 block whose centre is 8 mm out, over a footprint 5 mm from centre to edge
    load = box((6, 6, 20), (8, 0, 15))
    expected = (720 * 8 / (100 + 720)) / 5.0
    assert B.tip_ratio([base, load]) == pytest.approx(expected, rel=1e-6)
    assert expected > 1.0


def test_tip_ratio_refuses_an_open_mesh():
    open_box = box((2, 2, 2))
    open_box.update_faces(np.arange(len(open_box.faces) - 1))
    with pytest.raises(ValueError):
        B.tip_ratio(open_box)


def test_first_layer_area_of_a_box():
    assert B.first_layer_area(box((10, 20, 5))) == pytest.approx(200.0)


def test_first_layer_area_counts_a_hole_as_negative():
    ring = trimesh.creation.annulus(r_min=3, r_max=5, height=4, sections=256)
    assert B.first_layer_area(ring) == pytest.approx(math.pi * (25 - 9), rel=0.01)


def test_overhang_flat_box_and_t_shape():
    assert B.overhang_share(box((10, 10, 10))) == 0.0
    post = box((2, 2, 10), (0, 0, 5))
    slab = box((20, 20, 2), (0, 0, 11))
    t = trimesh.util.concatenate([post, slab])
    # the slab's underside over open air is 400 - 4 mm2 of a 1040 mm2 surface
    assert B.overhang_share(t) == pytest.approx(100 * 396 / 1040, abs=1.5)


def test_length_along_own_axis_ignores_rotation():
    rod = box((30, 4, 4))
    rod.apply_transform(tf.rotation_matrix(np.pi / 4, [0, 0, 1]))
    assert rod.extents[0] == pytest.approx(24.04, abs=0.01)    # what a world box would say
    assert B.length_along_axis(rod) == pytest.approx(30.0, abs=0.1)
    assert B.size_ratio(rod, box((15, 4, 4))) == pytest.approx(2.0, abs=0.01)
    assert B.size_ratio(box((2, 2, 2)), box((1, 2, 2)), by="volume") == pytest.approx(2.0)


def test_axis_angle_is_unsigned():
    rod = box((30, 4, 4))
    rod.apply_transform(tf.rotation_matrix(np.radians(30), [0, 0, 1]))
    assert B.axis_angle(B.principal_axis(rod), [1, 0, 0]) == pytest.approx(30.0, abs=0.5)
    assert B.axis_angle([1, 0, 0], [-1, 0, 0]) == pytest.approx(0.0)


def test_bow_straight_and_bent():
    rod = trimesh.creation.capsule(height=30, radius=1.4, count=[16, 16])
    assert B.bow(rod) < 0.2
    a = box((20, 2, 2), (10, 0, 0))
    b = a.copy()
    a.apply_transform(tf.rotation_matrix(np.radians(20), [0, 0, 1]))
    b.apply_transform(tf.rotation_matrix(np.radians(160), [0, 0, 1]))
    # the corner stands 20 sin(20 deg) = 6.8 mm off the chord, and banding rounds it a little
    assert 5.0 < B.bow(trimesh.util.concatenate([a, b])) < 7.0


def test_out_of_round_plain_and_lettered():
    cup = trimesh.creation.cylinder(radius=5, height=20, sections=128)
    assert B.out_of_round(cup, axis=[0, 0, 1]) < 0.01
    lettered = trimesh.util.concatenate([cup, box((1, 3, 3), (5.4, 0, 0))])
    assert B.out_of_round(lettered, axis=[0, 0, 1]) > 0.03


def test_tunnel_count():
    assert B.tunnel_count(box((2, 2, 2))) == 0
    assert B.tunnel_count(trimesh.creation.torus(major_radius=10, minor_radius=3)) == 1


def test_brightness_of_a_known_picture():
    img = np.zeros((50, 50, 3), np.uint8)
    img[:] = (23, 26, 28)
    img[10:30, 10:30] = 128
    assert B.brightness(img, (23, 26, 28)) == pytest.approx(128 / 255)
    with pytest.raises(ValueError):
        B.brightness(np.full((50, 50, 3), 23, np.uint8), (23, 23, 23))


def test_view_brightness_dark_from_below_without_headlight():
    cube = box((10, 10, 10))
    lit = B.view_brightness(cube, -90, 10)
    dark = B.view_brightness(cube, -90, -60, headlight=0.0)
    assert 0.0 < dark < lit <= 1.0
    assert dark < 0.34 < lit


# -------------------------------------------------------------------------------------- board
def healthy():
    """A body, a lid 0.3 mm above it, and anything added later as extra load."""
    return {"body": box((10, 6, 4), (0, 0, 2)), "lid": box((10, 6, 1), (0, 0, 4.8)), "extra": []}


def small_board():
    board = B.Board([
        B.Row("symmetric", "the body must be symmetric left to right",
              lambda d: B.mirror_asymmetry(d["body"], [1, 0, 0]), B.at_most(0.1), "mm",
              look_at="the front view"),
        B.Row("lid clears", "the lid must open without scraping",
              lambda d: B.gap(d["body"], d["lid"]), B.between(0.2, 0.5), "mm",
              look_at="the side view"),
        B.Row("stands", "it must stand on its own",
              lambda d: B.tip_ratio([d["body"], d["lid"], *d["extra"]]), B.at_most(0.75)),
    ])

    @board.fault("symmetric")
    def _bump(d):
        d["body"] = trimesh.util.concatenate([d["body"], box((2, 2, 2), (6, 0, 2))])

    @board.fault("lid clears")
    def _drop_lid(d):
        d["lid"].apply_translation([0, 0, -0.3])

    @board.fault("stands")
    def _overhang(d):
        d["extra"].append(box((6, 6, 20), (12, 0, 16)))

    return board


def test_small_board_passes():
    results = small_board().run(healthy())
    assert [r.verdict for r in results] == [B.GREEN] * 3
    assert results[1].value == pytest.approx(0.3, abs=1e-9)


def test_fault_injection_turns_each_row_red_and_back():
    proofs = small_board().prove(healthy())
    assert [p.status for p in proofs] == [B.PROVEN] * 3
    assert all(p.moved for p in proofs)
    assert all(p.also_red == [] for p in proofs)
    lid = proofs[1]
    assert lid.healthy == pytest.approx(0.3, abs=1e-9) and lid.faulted == 0.0


def test_a_row_whose_measure_raises_is_unknown():
    def broken(_d):
        raise RuntimeError("no such part")
    board = B.Board([B.Row("broken", "anything", broken, B.at_most(1.0))])
    (r,) = board.run(healthy())
    assert r.verdict == B.UNKNOWN
    assert "RuntimeError" in r.why


def test_no_value_is_unknown_never_green():
    board = B.Board([B.Row("none", "x", lambda d: None, B.at_most(1.0)),
                     B.Row("nan", "x", lambda d: float("nan"), B.at_most(1.0)),
                     B.Row("no bar", "x", lambda d: 0.0),
                     B.Row("bad bar", "x", lambda d: "text", B.at_most(1.0)),
                     B.Row("a person decides", "it looks right")])
    assert all(r.verdict == B.UNKNOWN for r in board.run(healthy()))


def test_a_row_without_a_fault_is_unproven():
    board = small_board()
    board.add(B.Row("lid is thin", "the lid must be thin",
                    lambda d: float(d["lid"].extents[2]), B.at_most(2.0), "mm"))
    board.add(B.Row("looks right", "a person must like it"))
    proofs = {p.name: p for p in board.prove(healthy())}
    assert proofs["lid is thin"].status == B.UNPROVEN
    assert proofs["looks right"].status == B.UNPROVEN
    assert proofs["symmetric"].status == B.PROVEN
    assert "2 rows" not in B.Board.proof_table(list(proofs.values()))
    assert "3 of 5 rows proven" in B.Board.proof_table(list(proofs.values()))


def test_a_fault_the_row_does_not_see_is_missed():
    board = small_board()

    @board.fault("symmetric")
    def _nothing(d):
        d["lid"] = d["lid"].copy()          # touches the design, not what the row reads

    (p,) = [p for p in board.prove(healthy()) if p.name == "symmetric"]
    assert p.status == B.MISSED
    assert p.moved is False
    assert "never saw the fault" in p.why


def test_a_fault_that_changes_the_original_is_caught():
    board = small_board()
    shallow = lambda d: dict(d)              # noqa: E731 - the meshes are shared with the original
    proofs = {p.name: p for p in board.prove(healthy(), copy=shallow)}
    assert proofs["lid clears"].status == B.NOT_RESTORED


def test_a_red_baseline_cannot_be_proven():
    d = healthy()
    d["lid"].apply_translation([0, 0, 2.0])          # lid far off, the row is red from the start
    proofs = {p.name: p for p in small_board().prove(d)}
    assert proofs["lid clears"].status == B.BASELINE_NOT_GREEN


def test_a_raising_fault_is_reported():
    board = small_board()

    @board.fault("stands")
    def _bad(d):
        raise KeyError("arm")

    (p,) = [p for p in board.prove(healthy()) if p.name == "stands"]
    assert p.status == B.FAULT_RAISED


def test_rows_red_under_another_rows_fault_are_listed():
    board = small_board()

    @board.fault("symmetric")
    def _heavy_bump(d):
        # a lump fixed high on one side, clear of the floor, breaks the symmetry and the stance
        d["body"] = trimesh.util.concatenate([d["body"], box((8, 6, 4), (12, 0, 6))])

    (p,) = [p for p in board.prove(healthy()) if p.name == "symmetric"]
    assert p.status == B.PROVEN
    assert p.also_red == ["stands"]


def test_names_are_unique_and_faults_must_name_a_row():
    board = small_board()
    with pytest.raises(ValueError):
        board.add(B.Row("symmetric", "again"))
    with pytest.raises(KeyError):
        board.fault("no such row")


def test_table_shows_verdicts_values_and_where_to_look():
    d = healthy()
    d["lid"].apply_translation([0, 0, 2.0])
    text = B.Board.table(small_board().run(d))
    assert "red" in text and "lid clears" in text and "(needs 0.2 to 0.5)" in text
    assert "see the side view" in text
    assert "2 green, 1 red, 0 unknown" in text
