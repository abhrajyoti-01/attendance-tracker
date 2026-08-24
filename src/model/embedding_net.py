from typing import TYPE_CHECKING

import torch
import torch.nn as nn
import torch.nn.functional as F
from torchvision import models

if TYPE_CHECKING:
    import onnx  # noqa: F401


class EfficientNetB0Backbone(nn.Module):
    def __init__(self, pretrained: bool = True):
        super().__init__()
        self.efficientnet = models.efficientnet_b0(pretrained=pretrained)

        features = list(self.efficientnet.features.children())
        self.features = nn.Sequential(*features)
        self.avgpool = nn.AdaptiveAvgPool2d(1)
        self.neck = nn.Sequential(
            nn.Conv2d(1280, 512, kernel_size=1),
            nn.BatchNorm2d(512),
            nn.PReLU(512),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        x = self.features(x)
        x = self.neck(x)
        x = self.avgpool(x)
        x = torch.flatten(x, 1)
        return x


class MobileNetV3SmallBackbone(nn.Module):
    def __init__(self, pretrained: bool = True):
        super().__init__()
        mobilenet = models.mobilenet_v3_small(pretrained=pretrained)

        self.features = mobilenet.features
        self.avgpool = nn.AdaptiveAvgPool2d(1)
        self.neck = nn.Sequential(
            nn.Conv2d(576, 512, kernel_size=1),
            nn.BatchNorm2d(512),
            nn.PReLU(512),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        x = self.features(x)
        x = self.neck(x)
        x = self.avgpool(x)
        x = torch.flatten(x, 1)
        return x


class EmbeddingHead(nn.Module):
    def __init__(self, input_dim: int = 512, embedding_dim: int = 128, dropout: float = 0.1):
        super().__init__()
        self.head = nn.Sequential(
            nn.Linear(input_dim, 256),
            nn.BatchNorm1d(256),
            nn.PReLU(256),
            nn.Dropout(dropout),
            nn.Linear(256, embedding_dim),
            nn.BatchNorm1d(embedding_dim),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        x = self.head(x)
        return F.normalize(x, p=2, dim=1)


class EmbeddingNet(nn.Module):
    def __init__(
        self,
        backbone: str = "efficientnet_b0",
        embedding_dim: int = 128,
        pretrained: bool = True,
        dropout: float = 0.1,
    ):
        super().__init__()
        self.backbone_name = backbone
        self.embedding_dim = embedding_dim

        if backbone == "efficientnet_b0":
            self.backbone = EfficientNetB0Backbone(pretrained=pretrained)
            neck_output = 512
        elif backbone == "mobilenet_v3_small":
            self.backbone = MobileNetV3SmallBackbone(pretrained=pretrained)
            neck_output = 512
        else:
            raise ValueError(f"Unsupported backbone: {backbone}")

        self.head = EmbeddingHead(neck_output, embedding_dim, dropout)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        features = self.backbone(x)
        embeddings = self.head(features)
        return embeddings

    def get_embedding(self, x: torch.Tensor) -> torch.Tensor:
        return self.forward(x)

    def freeze_backbone(self):
        for param in self.backbone.parameters():
            param.requires_grad = False

    def unfreeze_backbone(self):
        for param in self.backbone.parameters():
            param.requires_grad = True

    def get_device(self) -> torch.device:
        return next(self.parameters()).device


def create_embedding_net(
    backbone: str = "efficientnet_b0",
    embedding_dim: int = 128,
    pretrained: bool = True,
    device: str = "cuda" if torch.cuda.is_available() else "cpu",
) -> EmbeddingNet:
    model = EmbeddingNet(backbone, embedding_dim, pretrained)
    model = model.to(device)
    return model


def export_to_onnx(
    model: nn.Module,
    output_path: str,
    input_size: tuple[int, int, int] = (1, 3, 160, 160),
    opset_version: int = 14,
) -> None:
    model.eval()
    dummy_input = torch.randn(*input_size)

    torch.onnx.export(
        model,
        dummy_input,
        output_path,
        input_names=["input"],
        output_names=["embedding"],
        dynamic_axes={
            "input": {0: "batch_size"},
            "embedding": {0: "batch_size"},
        },
        opset_version=opset_version,
    )


def load_onnx_model(onnx_path: str) -> "onnx.ModelProto":
    import onnx

    return onnx.load(onnx_path)


def validate_onnx_model(onnx_path: str) -> bool:
    import onnx
    from onnx import checker

    try:
        model = onnx.load(onnx_path)
        checker.check_model(model)
        return True
    except Exception:
        return False
