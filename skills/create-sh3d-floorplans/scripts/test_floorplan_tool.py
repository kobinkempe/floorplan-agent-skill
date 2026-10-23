#!/usr/bin/env python3

from __future__ import annotations

import copy
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
import xml.etree.ElementTree as ET

import floorplan_tool


def evidence(basis: str = "specified", confidence: float = 1.0) -> dict:
    return {
        "basis": basis,
        "confidence": confidence,
        "note": "Synthetic test geometry.",
    }


def valid_document() -> dict:
    entity_ids = [
        "test-project",
        "ground",
        "north-wall",
        "east-wall",
        "south-wall",
        "west-wall",
        "test-room",
        "entry-door",
        "test-table",
    ]
    return {
        "schema_version": "1.1.0",
        "project": {
            "id": "test-project",
            "name": "Synthetic test room",
            "description": "A deterministic exporter test.",
            "selected_level_id": "ground",
            "default_wall_thickness_cm": 15,
            "default_wall_height_cm": 280,
        },
        "coordinate_system": {
            "units": "cm",
            "origin": "plan-top-left",
            "x_axis": "right-east",
            "y_axis": "down-south",
            "z_axis": "up",
        },
        "provenance": {
            "source_type": "measured-drawing",
            "measurement_status": "measured",
            "sources": [
                {
                    "id": "test-source",
                    "kind": "drawing",
                    "uri": "memory:test",
                    "description": "Synthetic test source.",
                }
            ],
            "observations": [
                {
                    "id": "test-observation",
                    "source_id": "test-source",
                    "statement": "Synthetic measured rectangle.",
                    "confidence": 1,
                    "affects": entity_ids,
                }
            ],
            "assumptions": [],
            "disclaimer": "Synthetic test only; not a construction document.",
        },
        "levels": [
            {
                "id": "ground",
                "name": "Ground",
                "elevation_cm": 0,
                "floor_thickness_cm": 20,
                "ceiling_height_cm": 280,
                "elevation_index": 0,
                "visible": True,
                "viewable": True,
                "evidence": evidence(),
            }
        ],
        "cameras": [],
        "walls": [
            {
                "id": "north-wall",
                "level_id": "ground",
                "name": "North wall",
                "role": "exterior",
                "start": {"x_cm": 0, "y_cm": 0},
                "end": {"x_cm": 500, "y_cm": 0},
                "thickness_cm": 15,
                "height_cm": 280,
                "color": "#D8D3C8",
                "evidence": evidence(),
            },
            {
                "id": "east-wall",
                "level_id": "ground",
                "name": "East wall",
                "role": "exterior",
                "start": {"x_cm": 500, "y_cm": 0},
                "end": {"x_cm": 500, "y_cm": 400},
                "thickness_cm": 15,
                "height_cm": 280,
                "color": "#D8D3C8",
                "evidence": evidence(),
            },
            {
                "id": "south-wall",
                "level_id": "ground",
                "name": "South wall",
                "role": "exterior",
                "start": {"x_cm": 500, "y_cm": 400},
                "end": {"x_cm": 0, "y_cm": 400},
                "thickness_cm": 15,
                "height_cm": 280,
                "color": "#D8D3C8",
                "evidence": evidence(),
            },
            {
                "id": "west-wall",
                "level_id": "ground",
                "name": "West wall",
                "role": "exterior",
                "start": {"x_cm": 0, "y_cm": 400},
                "end": {"x_cm": 0, "y_cm": 0},
                "thickness_cm": 15,
                "height_cm": 280,
                "color": "#D8D3C8",
                "evidence": evidence(),
            },
        ],
        "spaces": [
            {
                "id": "test-room",
                "level_id": "ground",
                "name": "Test room",
                "kind": "room",
                "polygon": [
                    {"x_cm": 7.5, "y_cm": 7.5},
                    {"x_cm": 492.5, "y_cm": 7.5},
                    {"x_cm": 492.5, "y_cm": 392.5},
                    {"x_cm": 7.5, "y_cm": 392.5},
                ],
                "floor": {"enabled": True, "color": "#E8E0D0", "shininess": 0.1},
                "ceiling": {"enabled": True, "color": "#FFFFFF", "shininess": 0},
                "area_visible": True,
                "evidence": evidence(),
            }
        ],
        "openings": [
            {
                "id": "entry-door",
                "wall_id": "south-wall",
                "name": "Entry door",
                "type": "door",
                "center_offset_cm": 250,
                "width_cm": 90,
                "height_cm": 210,
                "sill_height_cm": 0,
                "depth_cm": 15,
                "color": "#8A5A34",
                "swing": {
                    "hinge": "left",
                    "direction": "in",
                    "angle_degrees": 90,
                },
                "evidence": evidence(),
            }
        ],
        "objects": [
            {
                "id": "test-table",
                "level_id": "ground",
                "name": "Test table",
                "category": "table",
                "shape": "box",
                "center": {"x_cm": 250, "y_cm": 200},
                "width_cm": 180,
                "depth_cm": 90,
                "height_cm": 75,
                "elevation_cm": 0,
                "angle_degrees": 0,
                "color": "#6F4A2D",
                "movable": True,
                "evidence": evidence(),
            }
        ],
    }


class FloorplanToolTests(unittest.TestCase):
    def test_schema_is_valid_json(self) -> None:
        schema_path = Path(__file__).parents[1] / "references" / "floorplan.schema.json"
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        self.assertEqual(schema["$schema"], "https://json-schema.org/draft/2020-12/schema")
        self.assertFalse(schema["additionalProperties"])

    def test_valid_document_builds_and_inspects(self) -> None:
        document = valid_document()
        validation = floorplan_tool.validate_document(document)
        self.assertTrue(validation.valid, validation.as_dict())
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sh3d_path = root / "test.sh3d"
            svg_path = root / "test.svg"
            png_path = root / "test.png"
            floorplan_tool.build_sh3d(document, sh3d_path)
            floorplan_tool.render_svg(document, svg_path)
            floorplan_tool.render_png(document, png_path)
            inspection = floorplan_tool.inspect_sh3d(sh3d_path)
            self.assertTrue(inspection["valid"], inspection)
            self.assertEqual(inspection["counts"]["level"], 1)
            self.assertEqual(inspection["counts"]["wall"], 4)
            self.assertEqual(inspection["counts"]["doorOrWindow"], 1)
            self.assertTrue(svg_path.exists())
            self.assertTrue(png_path.exists())

    def test_xml_colors_are_argb_hex_and_camera_is_explicit(self) -> None:
        document = valid_document()
        with tempfile.TemporaryDirectory() as directory:
            sh3d_path = Path(directory) / "test.sh3d"
            floorplan_tool.build_sh3d(document, sh3d_path)
            with zipfile.ZipFile(sh3d_path) as archive:
                root = ET.fromstring(archive.read("Home.xml"))

            # Environment colours carry a 00 alpha byte, matching what Sweet
            # Home 3D writes for its own files.
            self.assertEqual(root.find("environment").get("groundColor"), "00A8A8A8")
            self.assertEqual(root.find("wall").get("leftSideColor"), "FFD8D3C8")
            self.assertEqual(root.find("room").get("floorColor"), "FFE8E0D0")
            self.assertEqual(root.find("doorOrWindow").get("color"), "FF8A5A34")
            self.assertEqual(root.find("pieceOfFurniture").get("color"), "FF6F4A2D")

            camera = root.find("camera")
            self.assertIsNotNone(camera)
            self.assertEqual(camera.get("attribute"), "topCamera")
            self.assertGreater(float(camera.get("z")), 0)
            self.assertEqual(root.get("camera"), "topCamera")

    def test_source_match_camera_light_and_models_are_exported(self) -> None:
        document = valid_document()
        document["cameras"] = [
            {
                "id": "source-camera",
                "name": "Source photo match",
                "mode": "observer",
                "position": {"x_cm": 250, "y_cm": 370, "z_cm": 155},
                "target": {"x_cm": 250, "y_cm": 20, "z_cm": 125},
                "field_of_view_degrees": 63,
                "lens": "pinhole",
                "active": True,
                "matches_source_id": "test-source",
                "evidence": evidence("estimated", 0.55),
            }
        ]
        document["objects"].append(
            {
                "id": "test-light",
                "level_id": "ground",
                "name": "Test light",
                "category": "light",
                "shape": "chandelier",
                "center": {"x_cm": 250, "y_cm": 200},
                "width_cm": 80,
                "depth_cm": 80,
                "height_cm": 60,
                "elevation_cm": 190,
                "angle_degrees": 0,
                "color": "#E8C67A",
                "light_power": 0.72,
                "light_color": "#FFD79A",
                "movable": False,
                "evidence": evidence(),
            }
        )
        with tempfile.TemporaryDirectory() as directory:
            sh3d_path = Path(directory) / "test.sh3d"
            floorplan_tool.build_sh3d(document, sh3d_path)
            inspection = floorplan_tool.inspect_sh3d(sh3d_path)
            self.assertTrue(inspection["valid"], inspection)
            # Parameterised shapes key on a per-variant name, not the bare shape.
            registry = floorplan_tool.model_registry(document)
            chandelier_path = next(
                path for key, path in registry.items() if key.startswith("chandelier")
            )
            with zipfile.ZipFile(sh3d_path) as archive:
                root = ET.fromstring(archive.read("Home.xml"))
                chandelier = archive.read(chandelier_path).decode("utf-8")

            self.assertEqual(root.get("version"), "7000")
            self.assertEqual(root.get("camera"), "observerCamera")
            self.assertIsNotNone(root.find("observerCamera[@attribute='observerCamera']"))
            self.assertIsNotNone(root.find("observerCamera[@attribute='storedCamera']"))
            light = root.find("light")
            self.assertIsNotNone(light)
            self.assertEqual(light.get("power"), "0.72")
            self.assertIsNotNone(light.find("lightSource"))
            self.assertIn(chandelier_path, inspection["entries"])
            self.assertIn("\nf ", chandelier)

    def test_every_model_lives_in_its_own_directory(self) -> None:
        """Sweet Home 3D resolves a model through its containing directory.

        Sharing one directory makes it treat every model as the same bundle and
        draw all furniture with whichever OBJ it finds first.
        """
        document = valid_document()
        files = floorplan_tool.collect_model_files(document)
        objs = [name for name in files if name.endswith(".obj")]
        self.assertGreater(len(objs), 1)
        directories = [name.rsplit("/", 1)[0] for name in objs]
        self.assertEqual(
            len(set(directories)), len(objs), "two models share a directory"
        )
        for name in objs:
            directory = name.rsplit("/", 1)[0]
            stem = Path(name).stem
            self.assertIn(f"{directory}/{stem}.mtl", files)

    def test_xml_model_paths_all_exist_in_the_archive(self) -> None:
        document = valid_document()
        files = floorplan_tool.collect_model_files(document)
        root = ET.fromstring(floorplan_tool.build_home_xml(document))
        referenced = {
            element.get("model")
            for element in root.iter()
            if element.get("model") is not None
        }
        self.assertTrue(referenced)
        for path in referenced:
            self.assertIn(path, files, f"XML references missing model {path}")

    def test_procedural_box_is_y_up_with_one_face_per_side(self) -> None:
        model = floorplan_tool._box_obj()
        vertices = [
            tuple(map(float, line.split()[1:]))
            for line in model.splitlines()
            if line.startswith("v ")
        ]
        self.assertEqual({vertex[1] for vertex in vertices}, {0.0, 1.0})
        self.assertEqual({vertex[2] for vertex in vertices}, {-0.5, 0.5})
        faces = [line for line in model.splitlines() if line.startswith("f ")]
        self.assertEqual(len(faces), 6)

    def test_no_model_emits_a_face_twice(self) -> None:
        """Coplanar duplicate faces z-fight and band every surface with stripes.

        Two-sided visibility must come from backFaceShown, not a second winding.
        """
        for name, body in floorplan_tool.BASE_MODEL_FILES.items():
            faces = [
                tuple(line.split()[1:])
                for line in body.splitlines()
                if line.startswith("f ")
            ]
            seen = {frozenset(face) for face in faces}
            self.assertEqual(
                len(seen), len(faces), f"{name} emits the same face more than once"
            )

    def test_only_referenced_models_are_embedded_each_with_a_material(self) -> None:
        document = valid_document()
        models = floorplan_tool.collect_model_files(document)
        objs = [name for name in models if name.endswith(".obj")]
        self.assertTrue(objs)
        # Nothing the document does not use should be carried along.
        self.assertLess(len(objs), len(floorplan_tool.BASE_MODEL_FILES))
        for name in objs:
            material = name[: -len(".obj")] + ".mtl"
            self.assertIn(material, models)
            body = models[name]
            self.assertIn(f"mtllib {Path(material).name}", body)
            self.assertIn(f"usemtl {floorplan_tool.MATERIAL_NAME}", body)
            self.assertIn(f"newmtl {floorplan_tool.MATERIAL_NAME}", models[material])

    def test_shape_params_produce_distinct_models(self) -> None:
        document = valid_document()
        base = copy.deepcopy(document["objects"][0])
        for index, columns in enumerate((2, 5)):
            piece = copy.deepcopy(base)
            piece["id"] = f"ceiling-{index}"
            piece["shape"] = "coffered-ceiling"
            piece["shape_params"] = {"columns": columns, "rows": 3}
            document["objects"].append(piece)
        self.assertTrue(floorplan_tool.validate_document(document).valid)
        models = floorplan_tool.collect_model_files(document)
        ceilings = [n for n in models if "coffered-ceiling" in n and n.endswith(".obj")]
        self.assertEqual(len(ceilings), 2, ceilings)
        # More columns must mean more beams, not a silently reused mesh.
        by_columns = sorted(ceilings, key=lambda n: models[n].count("g beam-x-"))
        self.assertLess(
            models[by_columns[0]].count("g beam-x-"),
            models[by_columns[1]].count("g beam-x-"),
        )

    def test_shape_params_are_rejected_for_unparameterised_shapes(self) -> None:
        document = valid_document()
        document["objects"][0]["shape"] = "box"
        document["objects"][0]["shape_params"] = {"columns": 3}
        validation = floorplan_tool.validate_document(document)
        self.assertFalse(validation.valid)
        self.assertIn(
            "shape_params.unsupported", {issue["code"] for issue in validation.errors}
        )

    def test_embedded_models_are_triangles_with_explicit_normals(self) -> None:
        """Quads and missing normals leave the importer guessing.

        Sweet Home 3D's desktop loader mis-triangulates quads (visible as
        sawtooth gaps) and can generate inward normals that hollow a solid out
        under back-face culling.
        """
        document = valid_document()
        for name, body in floorplan_tool.collect_model_files(document).items():
            if not name.endswith(".obj"):
                continue
            faces = [l for l in body.splitlines() if l.startswith("f ")]
            self.assertTrue(faces, f"{name} has no faces")
            for face in faces:
                corners = face.split()[1:]
                self.assertEqual(len(corners), 3, f"{name} has a non-triangle face")
                for corner in corners:
                    self.assertIn("//", corner, f"{name} face lacks a normal")
            self.assertTrue(
                any(l.startswith("vn ") for l in body.splitlines()),
                f"{name} declares no vertex normals",
            )

    def test_every_shipped_model_group_is_wound_outward(self) -> None:
        """An inward-wound solid is culled and you see through it.

        The revolved generators wind opposite to the cuboid ones, so the
        finalizer reorients each group by signed volume. Without that, curved
        furniture renders as a hollow shell in Sweet Home 3D.
        """
        for name, raw in floorplan_tool.BASE_MODEL_FILES.items():
            finalized = floorplan_tool._finalize_obj(raw)
            vertices: list[tuple[float, float, float]] = []
            groups: dict[str, list[list[int]]] = {}
            current = "default"
            for line in finalized.splitlines():
                parts = line.split()
                if not parts:
                    continue
                if parts[0] == "v":
                    vertices.append(tuple(map(float, parts[1:4])))
                elif parts[0] == "g":
                    current = parts[1] if len(parts) > 1 else "default"
                elif parts[0] == "f":
                    groups.setdefault(current, []).append(
                        [int(token.split("/")[0]) for token in parts[1:]]
                    )
            for group, faces in groups.items():
                volume = floorplan_tool._group_signed_volume(vertices, faces)
                self.assertGreater(
                    volume, -1e-9, f"{name} group {group!r} is wound inward"
                )

    def test_shipped_models_are_uniformly_wound(self) -> None:
        """Mixed orientation is what actually breaks rendering.

        Whichever convention Sweet Home 3D wants, every group of every model
        must agree; a document mixing the two renders some furniture solid and
        the rest inside out.
        """
        document = valid_document()
        signs = set()
        for name, body in floorplan_tool.collect_model_files(document).items():
            if not name.endswith(".obj"):
                continue
            vertices: list[tuple[float, float, float]] = []
            groups: dict[str, list[list[int]]] = {}
            current = "default"
            for line in body.splitlines():
                parts = line.split()
                if not parts:
                    continue
                if parts[0] == "v":
                    vertices.append(tuple(map(float, parts[1:4])))
                elif parts[0] == "g":
                    current = parts[1] if len(parts) > 1 else "default"
                elif parts[0] == "f":
                    groups.setdefault(current, []).append(
                        [int(token.split("/")[0]) for token in parts[1:]]
                    )
            for group, faces in groups.items():
                volume = floorplan_tool._group_signed_volume(vertices, faces)
                self.assertNotAlmostEqual(
                    volume, 0.0, msg=f"{name}/{group} encloses no volume"
                )
                signs.add(volume > 0)
        self.assertEqual(len(signs), 1, "models disagree on winding direction")

    def test_every_catalogue_shape_is_closed_and_uniformly_wound(self) -> None:
        """Cover the whole catalogue, not just the shapes the fixture happens to use.

        The per-document winding tests only see models a sample plan
        references, so a new generator can ship inside out or with duplicate
        faces and stay green until someone authors a room that uses it.
        """
        signs: set[bool] = set()
        for shape in sorted(floorplan_tool.BUILTIN_SHAPES):
            table = floorplan_tool.SHAPE_PARAMETERS.get(shape)
            if table:
                # Defaults plus both extremes: parameter-driven geometry tends
                # to degenerate at the ends of its range, not in the middle.
                variants = [
                    {name: bounds[2] for name, bounds in table.items()},
                    {name: bounds[0] for name, bounds in table.items()},
                    {name: bounds[1] for name, bounds in table.items()},
                ]
            else:
                variants = [{}]
            for params in variants:
                label = f"{shape} {params}"
                geometry = (
                    floorplan_tool._build_parametric_obj(shape, params)
                    if params
                    else floorplan_tool.BASE_MODEL_FILES[f"models/{shape}.obj"]
                )
                body = floorplan_tool._finalize_obj(
                    geometry, floorplan_tool.FLIP_WINDING
                )
                vertices: list[tuple[float, float, float]] = []
                groups: dict[str, list[list[int]]] = {}
                current = "default"
                for line in body.splitlines():
                    parts = line.split()
                    if not parts:
                        continue
                    if parts[0] == "v":
                        vertices.append(tuple(map(float, parts[1:4])))
                    elif parts[0] == "g":
                        current = parts[1] if len(parts) > 1 else "default"
                    elif parts[0] == "f":
                        groups.setdefault(current, []).append(
                            [int(token.split("/")[0]) for token in parts[1:]]
                        )
                self.assertTrue(groups, f"{label} produced no geometry")
                raw_faces = [
                    tuple(line.split()[1:])
                    for line in geometry.splitlines()
                    if line.startswith("f ")
                ]
                self.assertEqual(
                    len({frozenset(face) for face in raw_faces}),
                    len(raw_faces),
                    f"{label} emits the same face more than once",
                )
                for group, faces in groups.items():
                    volume = floorplan_tool._group_signed_volume(vertices, faces)
                    self.assertNotAlmostEqual(
                        volume, 0.0, msg=f"{label} group {group!r} encloses no volume"
                    )
                    signs.add(volume > 0)
        self.assertEqual(len(signs), 1, "catalogue shapes disagree on winding direction")

    def test_solid_furniture_has_no_coplanar_overlaps(self) -> None:
        """Butted parts z-fight and band a surface into stripes.

        The exporter sets backFaceShown, so nothing is culled and two faces in
        one plane both get drawn. Merely stacking one part on another is
        enough, which is why this checks overlapping areas rather than
        duplicated faces.

        Scoped to the pieces with large flat surfaces. The two fan generators
        build a splay from stepped boxes that necessarily converge, and any
        banding there is a fraction of a bar wide; ``check-models`` reports
        them for anyone who wants to look.
        """
        solids = sorted(
            floorplan_tool.BUILTIN_SHAPES - {"deco-railing", "sunburst", "crown-moulding"}
        )
        offenders: dict[str, list] = {}
        for shape in solids:
            table = floorplan_tool.SHAPE_PARAMETERS.get(shape)
            params = {name: bounds[2] for name, bounds in table.items()} if table else {}
            findings = floorplan_tool.coplanar_overlaps(shape, params)
            if findings:
                offenders[shape] = findings
        self.assertEqual(
            offenders,
            {},
            "run: python floorplan_tool.py check-models",
        )

    def test_detailing_keeps_its_thickness_on_stretched_pieces(self) -> None:
        """Glazing bars and panel rails are real timber, not a fixed fraction.

        The unit cube is scaled per axis, so a 124 x 590 window used to get
        14 cm horizontal muntins against 3 cm vertical ones.
        """

        def thickness(body: str, group: str, width: float, height: float):
            points: list[tuple[float, float, float]] = []
            collecting = False
            for line in body.splitlines():
                if line.startswith("g "):
                    collecting = line[2:].strip() == group
                elif line.startswith("v ") and collecting:
                    points.append(tuple(float(v) for v in line.split()[1:4]))
            xs = [p[0] for p in points]
            ys = [p[1] for p in points]
            return (max(xs) - min(xs)) * width, (max(ys) - min(ys)) * height

        width, height = 124.0, 590.0
        params = floorplan_tool.resolve_geometry_params(
            "window", {"columns": 4, "rows": 12}, width, height
        )
        self.assertAlmostEqual(params["aspect"], 0.2102, places=3)
        body = floorplan_tool._paned_window_obj(params)
        vertical, _ = thickness(body, "muntin-v-1", width, height)
        _, horizontal = thickness(body, "muntin-h-1", width, height)
        self.assertAlmostEqual(vertical, horizontal, delta=0.5)

        params = floorplan_tool.resolve_geometry_params(
            "wall-panel", {"columns": 5, "rows": 1, "border_ratio": 0.1}, 800, 300
        )
        body = floorplan_tool._wall_panel_obj(params)
        stile, _ = thickness(body, "left-0-0", 800, 300)
        _, rail = thickness(body, "bottom-0-0", 800, 300)
        self.assertAlmostEqual(stile, rail, delta=1.5)

        # Two windows of different proportions must not share one mesh.
        tall = floorplan_tool._shape_model_name(
            "window",
            floorplan_tool.resolve_geometry_params("window", {"columns": 2, "rows": 2}, 100, 400),
        )
        wide = floorplan_tool._shape_model_name(
            "window",
            floorplan_tool.resolve_geometry_params("window", {"columns": 2, "rows": 2}, 400, 100),
        )
        self.assertNotEqual(tall, wide)

    def test_facing_is_checked_against_the_angle(self) -> None:
        """Orientation is the error that survives every other kind of review.

        A backwards sofa validates, renders, and still looks like furniture in
        plan. Only a declared facing catches it.
        """
        self.assertEqual(floorplan_tool.CARDINAL_ANGLES["south"], 0.0)
        for angle, expected in (
            (0.0, (0.0, 1.0)),
            (90.0, (-1.0, 0.0)),
            (180.0, (0.0, -1.0)),
            (270.0, (1.0, 0.0)),
        ):
            vector = floorplan_tool.facing_vector(angle)
            self.assertAlmostEqual(vector[0], expected[0], places=6)
            self.assertAlmostEqual(vector[1], expected[1], places=6)

        document = valid_document()
        sofa = {
            "id": "test-sofa",
            "level_id": "ground",
            "name": "Test sofa",
            "category": "sofa",
            "shape": "sofa",
            "center": {"x_cm": 250, "y_cm": 60},
            "width_cm": 200,
            "depth_cm": 90,
            "height_cm": 85,
            "elevation_cm": 0,
            "angle_degrees": 180,
            "color": "#40603F",
            "movable": True,
            "facing": "south",
            "evidence": evidence(),
        }
        document["objects"].append(sofa)
        document["provenance"]["observations"][0]["affects"].append("test-sofa")

        # 180 faces north, into the wall it stands against: both checks fire.
        result = floorplan_tool.validate_document(document)
        self.assertIn("facing.mismatch", {e["code"] for e in result.errors})
        self.assertIn("facing.into-wall", {w["code"] for w in result.warnings})

        sofa["angle_degrees"] = 0
        result = floorplan_tool.validate_document(document)
        self.assertEqual(result.errors, [])
        self.assertEqual(
            [w for w in result.warnings if w["code"] == "facing.into-wall"], []
        )

        # A facing point resolves to the angle that aims at it.
        sofa["facing"] = {"x_cm": 250, "y_cm": 300}
        self.assertAlmostEqual(
            floorplan_tool.angle_for_facing((250, 60), sofa["facing"]), 0.0, places=6
        )

    def test_environment_is_author_controllable(self) -> None:
        """Lighting is the strongest control over how a render reads."""
        document = valid_document()
        self.assertTrue(floorplan_tool.validate_document(document).valid)

        document["environment"] = {"light_color": "#808080", "walls_alpha": 0.5}
        result = floorplan_tool.validate_document(document)
        self.assertEqual(result.errors, [])
        with tempfile.TemporaryDirectory() as directory:
            sh3d_path = Path(directory) / "env.sh3d"
            floorplan_tool.build_sh3d(document, sh3d_path)
            with zipfile.ZipFile(sh3d_path) as archive:
                environment = ET.fromstring(archive.read("Home.xml")).find("environment")
        self.assertEqual(environment.get("lightColor"), "00808080")
        self.assertEqual(environment.get("wallsAlpha"), "0.5")
        # Unset keys keep the defaults rather than disappearing.
        self.assertEqual(environment.get("ceillingLightColor"), "00D0D0D0")

        document["environment"] = {"light_color": "not-a-colour"}
        self.assertFalse(floorplan_tool.validate_document(document).valid)

    def test_unit_box_normals_point_outward(self) -> None:
        """A wrong sign here turns every solid inside out."""
        finalized = floorplan_tool._finalize_obj(floorplan_tool._box_obj())
        normals = {
            tuple(round(float(v)) for v in line.split()[1:])
            for line in finalized.splitlines()
            if line.startswith("vn ")
        }
        self.assertEqual(
            normals,
            {(0, 0, -1), (0, 0, 1), (0, -1, 0), (1, 0, 0), (0, 1, 0), (-1, 0, 0)},
        )

    def test_shipped_templates_validate_cleanly(self) -> None:
        """A template that does not validate teaches the wrong shape."""
        templates = sorted(
            (Path(__file__).parent.parent / "references" / "templates").glob("*.json")
        )
        self.assertTrue(templates, "no templates found")
        for path in templates:
            document = json.loads(path.read_text(encoding="utf-8"))
            validation = floorplan_tool.validate_document(document)
            self.assertEqual(
                validation.errors, [], f"{path.name} has validation errors"
            )
            self.assertEqual(
                validation.warnings, [], f"{path.name} has validation warnings"
            )

    def test_camera_outside_the_plan_is_rejected(self) -> None:
        """The camera standing outside the walls renders the room cut open."""
        document = valid_document()
        document["cameras"] = [
            {
                "id": "source-match",
                "name": "Source photo match",
                "mode": "observer",
                "position": {"x_cm": 9000, "y_cm": 9000, "z_cm": 160},
                "target": {"x_cm": 250, "y_cm": 20, "z_cm": 120},
                "field_of_view_degrees": 70,
                "lens": "pinhole",
                "active": True,
                "matches_source_id": "test-source",
                "evidence": evidence("estimated", 0.5),
            }
        ]
        validation = floorplan_tool.validate_document(document)
        self.assertIn(
            "camera.outside-space", {issue["code"] for issue in validation.errors}
        )
        # Moving it inside the room clears the error.
        document["cameras"][0]["position"] = {"x_cm": 250, "y_cm": 350, "z_cm": 160}
        self.assertNotIn(
            "camera.outside-space",
            {issue["code"] for issue in floorplan_tool.validate_document(document).errors},
        )

    def test_opening_must_fit_wall(self) -> None:
        document = valid_document()
        document["openings"][0]["center_offset_cm"] = 490
        validation = floorplan_tool.validate_document(document)
        self.assertFalse(validation.valid)
        self.assertIn("opening.outside-wall", {issue["code"] for issue in validation.errors})

    def test_space_must_not_self_intersect(self) -> None:
        document = valid_document()
        document["spaces"][0]["polygon"] = [
            {"x_cm": 0, "y_cm": 0},
            {"x_cm": 400, "y_cm": 400},
            {"x_cm": 400, "y_cm": 0},
            {"x_cm": 0, "y_cm": 400},
        ]
        validation = floorplan_tool.validate_document(document)
        self.assertFalse(validation.valid)
        self.assertIn("space.self-intersection", {issue["code"] for issue in validation.errors})

    def test_void_must_hide_surfaces(self) -> None:
        document = valid_document()
        document["spaces"][0]["kind"] = "void"
        validation = floorplan_tool.validate_document(document)
        self.assertFalse(validation.valid)
        codes = {issue["code"] for issue in validation.errors}
        self.assertIn("void.floor", codes)
        self.assertIn("void.ceiling", codes)

    def test_duplicate_geometry_ids_are_rejected(self) -> None:
        document = valid_document()
        duplicate = copy.deepcopy(document["objects"][0])
        document["objects"].append(duplicate)
        validation = floorplan_tool.validate_document(document)
        self.assertFalse(validation.valid)
        self.assertIn("id.duplicate", {issue["code"] for issue in validation.errors})


if __name__ == "__main__":
    unittest.main()
