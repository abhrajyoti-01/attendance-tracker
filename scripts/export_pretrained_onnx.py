"""Export a pretrained FaceNet (InceptionResNetV1, VGGFace2) to ONNX.

The exported model consumes standardized 1x3x160x160 float tensors and emits
L2-normalized embeddings of dimension 512. Standardization ((x-0.5)/0.5) is NOT
baked into the graph; the API's EmbeddingEngine applies it before inference.

Usage:
    python scripts/export_pretrained_onnx.py [--output models/exported/embedding_net.onnx]
"""

import argparse
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

DEFAULT_OUTPUT = BASE_DIR / "models" / "exported" / "embedding_net.onnx"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--opset", type=int, default=14)
    parser.add_argument("--validate", action="store_true", help="Run an ONNX Runtime smoke test")
    args = parser.parse_args()

    import torch
    from facenet_pytorch import InceptionResnetV1

    print("Loading pretrained InceptionResnetV1 (vggface2)...")
    model = InceptionResnetV1(pretrained="vggface2").eval()

    dummy = torch.randn(1, 3, 160, 160)

    args.output.parent.mkdir(parents=True, exist_ok=True)

    print(f"Exporting to {args.output} (opset {args.opset})...")
    with torch.no_grad():
        torch.onnx.export(
            model,
            dummy,
            str(args.output),
            input_names=["input.1"],
            output_names=["embedding"],
            opset_version=args.opset,
            do_constant_folding=True,
            dynamic_axes={
                "input.1": {0: "batch"},
                "embedding": {0: "batch"},
            },
        )
    print("Export complete.")

    if args.validate:
        import numpy as np
        import onnxruntime as ort

        session = ort.InferenceSession(str(args.output), providers=["CPUExecutionProvider"])
        sample = np.random.rand(2, 3, 160, 160).astype(np.float32)
        out = session.run(None, {session.get_inputs()[0].name: sample})[0]
        norms = np.linalg.norm(out, axis=1)
        assert out.shape == (2, 512), f"Unexpected output shape: {out.shape}"
        assert np.allclose(norms, 1.0, atol=1e-3), f"Embeddings not normalized: {norms}"
        print(f"Validation OK: shape={out.shape}, norms={norms.round(4)}")

    size_mb = args.output.stat().st_size / (1024 * 1024)
    print(f"Done: {args.output} ({size_mb:.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
