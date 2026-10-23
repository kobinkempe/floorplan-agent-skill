#!/usr/bin/env python3
"""Prepare a local Sweet Home 3D JS Viewer scene for render inspection.

The official GPL viewer is intentionally not bundled with this skill. Download
it from Sweet Home 3D, then point this script at the extracted directory.
"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path


SCRIPT_NAMES = (
    "big.min.js",
    "gl-matrix-min.js",
    "jszip.min.js",
    "core.min.js",
    "geom.min.js",
    "stroke.min.js",
    "batik-svgpathparser.min.js",
    "jsXmlSaxParser.min.js",
    "triangulator.min.js",
    "viewmodel.min.js",
    "viewhome.min.js",
)


def build_html(home_name: str, camera: str | None, width: int, height: int) -> str:
    script_tags = "\n".join(
        f'  <script src="lib/{name}"></script>' for name in SCRIPT_NAMES
    )
    camera_option = (
        f",\n        camera: {json.dumps(camera)}" if camera is not None else ""
    )
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Sweet Home 3D render inspection</title>
  <script>
    // The viewer creates its own WebGL context. Without preserveDrawingBuffer
    // the back buffer is undefined once the frame has been composited, so
    // canvas.toDataURL() can return a torn or cleared image. Forcing the flag
    // before the viewer loads makes headless capture reproducible.
    (function () {{
      const getContext = HTMLCanvasElement.prototype.getContext;
      HTMLCanvasElement.prototype.getContext = function (type, attributes) {{
        if (type === "webgl" || type === "webgl2" || type === "experimental-webgl") {{
          attributes = Object.assign({{}}, attributes, {{ preserveDrawingBuffer: true }});
        }}
        return getContext.call(this, type, attributes);
      }};
    }})();
  </script>
{script_tags}
  <style>
    html, body {{ margin: 0; width: 100%; height: 100%; overflow: hidden; background: #dceaf5; }}
    /* Pin the canvas to its exact drawing-buffer size. Stretching it to the
       viewport would make the rendered aspect ratio depend on the browser
       window, which defeats side-by-side comparison against a source image. */
    #viewerCanvas {{
      display: block; position: fixed; left: 0; top: 0;
      width: {width}px; height: {height}px; outline: none; touch-action: none;
    }}
    #status {{
      position: fixed; left: 12px; top: 12px; padding: 6px 9px;
      color: #17202a; background: rgba(255,255,255,.82);
      border-radius: 5px; font: 12px/1.3 system-ui, sans-serif;
    }}
  </style>
</head>
<body data-status="loading">
  <canvas id="viewerCanvas" width="{width}" height="{height}" tabindex="1"></canvas>
  <div id="status">Loading home…</div>
  <script>
    window.renderReady = false;
    window.renderError = null;
    const status = document.getElementById("status");
    function fail(error) {{
      window.renderError = String(error && (error.stack || error.message) || error);
      document.body.dataset.status = "error";
      status.textContent = window.renderError;
    }}
    // The viewer draws its first frame as soon as the home is parsed, while
    // furniture models are still being fetched from the archive. Those pieces
    // are drawn as untinted placeholder boxes. With roundsPerMinute 0 there is
    // no animation loop, so nothing ever redraws and a screenshot captures the
    // stale frame -- geometry and colour both wrong. Forcing drawScene() after
    // loading completes is what makes a capture reflect the real scene.
    window.forceRedraw = function () {{
      const canvas3D = component.getComponent3D().canvas3D;
      canvas3D.drawScene();
      return canvas3D.isLoadingCompleted ? canvas3D.isLoadingCompleted() : null;
    }};
    // One call per render iteration: wait until every model has actually
    // finished loading, force a redraw, then hand back the PNG. Capturing on
    // the progress callback alone is not enough -- the callback can fire while
    // the last models are still being attached, and the screenshot then shows
    // untinted placeholder boxes. Resolves to a data URL; POST it wherever the
    // iteration loop wants it.
    window.captureWhenReady = function () {{
      return new Promise(function (resolve, reject) {{
        let attempts = 0;
        (function poll() {{
          attempts += 1;
          let loaded = false;
          try {{
            loaded = component.getComponent3D().canvas3D.isLoadingCompleted();
          }} catch (error) {{ /* not constructed yet */ }}
          if (window.renderError) {{
            reject(new Error(window.renderError));
          }} else if ((window.renderReady && loaded) || attempts > 240) {{
            window.forceRedraw();
            setTimeout(function () {{
              window.forceRedraw();
              resolve(document.getElementById("viewerCanvas").toDataURL("image/png"));
            }}, 500);
          }} else {{
            setTimeout(poll, 250);
          }}
        }})();
      }});
    }};
    function progress(part, info, percentage) {{
      status.textContent = `${{part}} ${{Math.floor((percentage || 0) * 100)}}%`;
      if (part === Node3D.READING_MODEL && percentage === 1) {{
        setTimeout(function () {{
          try {{
            window.forceRedraw();
          }} catch (error) {{
            fail(error);
            return;
          }}
          window.renderReady = true;
          document.body.dataset.status = "ready";
          status.style.display = "none";
        }}, 400);
      }}
    }}
    const component = viewHome(
      "viewerCanvas",
      {json.dumps(home_name)},
      fail,
      progress,
      {{
        roundsPerMinute: 0,
        navigationPanel: "none",
        activateCameraSwitchKey: false{camera_option}
      }}
    );
    window.viewerComponent = component;
  </script>
</body>
</html>
"""


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Prepare a self-contained local SH3D JS Viewer test scene."
    )
    parser.add_argument("--viewer-dir", required=True, type=Path)
    parser.add_argument("--sh3d", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--camera")
    parser.add_argument("--width", type=int, default=1024)
    parser.add_argument("--height", type=int, default=1024)
    args = parser.parse_args()

    viewer_dir = args.viewer_dir.resolve()
    sh3d_path = args.sh3d.resolve()
    output_dir = args.output_dir.resolve()
    source_lib = viewer_dir / "lib"
    if not source_lib.is_dir():
        parser.error(f"{viewer_dir} does not contain the viewer lib directory")
    if not sh3d_path.is_file():
        parser.error(f"{sh3d_path} is not a file")
    if args.width < 64 or args.height < 64:
        parser.error("width and height must each be at least 64 pixels")

    output_dir.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source_lib, output_dir / "lib", dirs_exist_ok=True)
    for pattern in ("LICENSE*", "COPYING*", "THIRDPARTY-LICENSE*"):
        for source in viewer_dir.glob(pattern):
            if source.is_file():
                shutil.copy2(source, output_dir / source.name)

    home_name = "scene.sh3d"
    shutil.copy2(sh3d_path, output_dir / home_name)
    (output_dir / "index.html").write_text(
        build_html(home_name, args.camera, args.width, args.height),
        encoding="utf-8",
    )
    print(output_dir / "index.html")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
