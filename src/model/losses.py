import torch
import torch.nn as nn
import torch.nn.functional as F


class ArcFaceLoss(nn.Module):
    def __init__(
        self,
        embedding_dim: int = 128,
        num_classes: int = 1000,
        margin: float = 0.5,
        scale: float = 64.0,
        easy_margin: bool = False,
    ):
        super().__init__()
        self.embedding_dim = embedding_dim
        self.num_classes = num_classes
        self.margin = margin
        self.scale = scale
        self.easy_margin = easy_margin

        self.cos_m = torch.cos(torch.tensor(margin))
        self.sin_m = torch.sin(torch.tensor(margin))
        self.th = torch.cos(torch.tensor(torch.pi - margin))
        self.mm = torch.sin(torch.tensor(torch.pi - margin)) * margin

        self.W = nn.Parameter(torch.FloatTensor(num_classes, embedding_dim))
        nn.init.xavier_uniform_(self.W)

    def forward(self, embeddings: torch.Tensor, labels: torch.Tensor) -> torch.Tensor:
        device = embeddings.device
        self.W.data = self.W.data.to(device)

        cosine = F.linear(F.normalize(embeddings), F.normalize(self.W))
        sine = torch.sqrt(1.0 - torch.pow(cosine, 2))
        phi = cosine * self.cos_m.to(device) - sine * self.sin_m.to(device)

        if self.easy_margin:
            phi = torch.where(cosine > 0, phi, cosine)
        else:
            phi = torch.where(cosine > self.th.to(device), phi, cosine - self.mm.to(device))

        one_hot = F.one_hot(labels, self.num_classes).float()
        output = (one_hot * phi) + ((1.0 - one_hot) * cosine)
        output *= self.scale

        return F.cross_entropy(output, labels)


class CosFaceLoss(nn.Module):
    def __init__(
        self,
        embedding_dim: int = 128,
        num_classes: int = 1000,
        margin: float = 0.35,
        scale: float = 64.0,
    ):
        super().__init__()
        self.embedding_dim = embedding_dim
        self.num_classes = num_classes
        self.margin = margin
        self.scale = scale

        self.W = nn.Parameter(torch.FloatTensor(num_classes, embedding_dim))
        nn.init.xavier_uniform_(self.W)

    def forward(self, embeddings: torch.Tensor, labels: torch.Tensor) -> torch.Tensor:
        device = embeddings.device
        self.W.data = self.W.data.to(device)

        cosine = F.linear(F.normalize(embeddings), F.normalize(self.W))
        one_hot = F.one_hot(labels, self.num_classes).float()
        output = cosine - one_hot * self.margin
        output *= self.scale

        return F.cross_entropy(output, labels)


class TripletLoss(nn.Module):
    def __init__(self, margin: float = 0.3):
        super().__init__()
        self.margin = margin

    def forward(self, embeddings: torch.Tensor, labels: torch.Tensor) -> torch.Tensor:
        pairwise_dist = torch.cdist(embeddings, embeddings, p=2)

        anchor_positive_dist = pairwise_dist.unsqueeze(2)
        anchor_negative_dist = pairwise_dist.unsqueeze(1)

        labels_equal = labels.unsqueeze(1) == labels.unsqueeze(0)
        labels_not_equal = ~labels_equal

        positive_dist = anchor_positive_dist * labels_equal.float().unsqueeze(2)
        negative_dist = anchor_negative_dist * labels_not_equal.float().unsqueeze(1)

        hardest_positive = positive_dist.max(dim=1)[0]
        hardest_negative = negative_dist.min(dim=1)[0]

        loss = F.relu(hardest_positive - hardest_negative + self.margin)
        return loss.mean()


class BatchHardTripletLoss(nn.Module):
    def __init__(self, margin: float = 0.3, squared: bool = False):
        super().__init__()
        self.margin = margin
        self.squared = squared

    def forward(self, embeddings: torch.Tensor, labels: torch.Tensor) -> torch.Tensor:
        pairwise_dist = _pairwise_distances(embeddings, squared=self.squared)

        mask_anchor_positive = _get_anchor_positive_mask(labels).float()
        mask_anchor_negative = _get_anchor_negative_mask(labels).float()

        anchor_positive_dist = mask_anchor_positive * pairwise_dist
        anchor_positive_dist = anchor_positive_dist.max(dim=1, keepdim=True)[0]

        max_anchor_negative_dist = pairwise_dist.max()
        anchor_negative_dist = pairwise_dist + max_anchor_negative_dist * (
            1.0 - mask_anchor_negative
        )
        anchor_negative_dist = anchor_negative_dist.min(dim=1, keepdim=True)[0]

        triplet_loss = F.relu(anchor_positive_dist - anchor_negative_dist + self.margin)

        num_positive_triplets = (triplet_loss > 1e-16).float().sum()
        triplet_loss = triplet_loss.sum() / (num_positive_triplets + 1e-16)

        return triplet_loss


class CombinedLoss(nn.Module):
    def __init__(
        self,
        embedding_dim: int = 128,
        num_classes: int = 1000,
        arcface_margin: float = 0.5,
        arcface_scale: float = 64.0,
        triplet_margin: float = 0.3,
        alpha: float = 0.7,
        beta: float = 0.3,
    ):
        super().__init__()
        self.arcface = ArcFaceLoss(embedding_dim, num_classes, arcface_margin, arcface_scale)
        self.triplet = BatchHardTripletLoss(triplet_margin)
        self.alpha = alpha
        self.beta = beta

    def forward(self, embeddings: torch.Tensor, labels: torch.Tensor) -> torch.Tensor:
        loss_arc = self.arcface(embeddings, labels)
        loss_tri = self.triplet(embeddings, labels)
        return self.alpha * loss_arc + self.beta * loss_tri


class CircleLoss(nn.Module):
    def __init__(
        self,
        embedding_dim: int = 128,
        num_classes: int = 1000,
        margin: float = 0.25,
        gamma: float = 256.0,
    ):
        super().__init__()
        self.embedding_dim = embedding_dim
        self.num_classes = num_classes
        self.margin = margin
        self.gamma = gamma

        self.W = nn.Parameter(torch.FloatTensor(num_classes, embedding_dim))
        nn.init.xavier_uniform_(self.W)

        self.O_p = 1 + margin
        self.O_n = -margin
        self.delta_p = 1 - margin
        self.delta_n = margin

    def forward(self, embeddings: torch.Tensor, labels: torch.Tensor) -> torch.Tensor:
        device = embeddings.device
        self.W.data = self.W.data.to(device)

        similarity = F.linear(F.normalize(embeddings), F.normalize(self.W))
        one_hot = F.one_hot(labels, self.num_classes).float()

        alpha_p = torch.clamp(self.O_p - similarity.detach(), min=0.0)
        alpha_n = torch.clamp(similarity.detach() - self.O_n, min=0.0)

        logit_p = similarity - 1.0e-5 * one_hot
        logit_n = similarity

        pos_loss = (
            -self.gamma
            * alpha_p
            * torch.log(torch.sigmoid(self.gamma * (logit_p - self.delta_p)))
            * one_hot
        )
        neg_loss = (
            -self.gamma
            * alpha_n
            * torch.log(torch.sigmoid(self.gamma * (self.delta_n - logit_n)))
            * (1.0 - one_hot)
        )

        loss = (pos_loss.sum() + neg_loss.sum()) / embeddings.size(0)
        return loss


class CenterLoss(nn.Module):
    def __init__(self, num_classes: int = 1000, embedding_dim: int = 128, alpha: float = 0.5):
        super().__init__()
        self.num_classes = num_classes
        self.embedding_dim = embedding_dim
        self.alpha = alpha

        self.centers = nn.Parameter(torch.FloatTensor(num_classes, embedding_dim))
        nn.init.xavier_uniform_(self.centers)

    def forward(self, embeddings: torch.Tensor, labels: torch.Tensor) -> torch.Tensor:
        batch_size = embeddings.size(0)

        centers_batch = self.centers[labels, :]
        diff = centers_batch - embeddings
        loss = self.alpha * (diff.pow(2).sum()) / (2.0 * batch_size)
        return loss


def _pairwise_distances(embeddings: torch.Tensor, squared: bool = False) -> torch.Tensor:
    dot_product = torch.matmul(embeddings, embeddings.t())
    squared_norm = torch.diag(dot_product)
    distances = squared_norm.unsqueeze(1) - 2.0 * dot_product + squared_norm.unsqueeze(0)
    distances = torch.clamp(distances, min=0.0)
    if not squared:
        mask = (distances == 0.0).float()
        distances = distances + mask * 1e-16
        distances = torch.sqrt(distances)
        distances = distances * (1.0 - mask)
    return distances


def _get_anchor_positive_mask(labels: torch.Tensor) -> torch.Tensor:
    indices_equal = torch.eye(labels.size(0), device=labels.device).bool()
    indices_not_equal = ~indices_equal
    labels_equal = labels.unsqueeze(1) == labels.unsqueeze(0)
    return labels_equal & indices_not_equal


def _get_anchor_negative_mask(labels: torch.Tensor) -> torch.Tensor:
    return ~(labels.unsqueeze(1) == labels.unsqueeze(0))


class AdaptiveTripletLoss(nn.Module):
    def __init__(self, margin: float = 0.3, adaptive: bool = True):
        super().__init__()
        self.margin = margin
        self.adaptive = adaptive

    def forward(self, embeddings: torch.Tensor, labels: torch.Tensor) -> torch.Tensor:
        pairwise_dist = torch.cdist(embeddings, embeddings, p=2)

        labels_equal = labels.unsqueeze(1) == labels.unsqueeze(0)
        labels_not_equal = ~labels_equal

        positive_pairs = pairwise_dist[labels_equal]
        negative_pairs = pairwise_dist[labels_not_equal]

        if self.adaptive and len(negative_pairs) > 0:
            neg_mean = negative_pairs.mean()
            adaptive_margin = self.margin + neg_mean.item() * 0.1
        else:
            adaptive_margin = self.margin

        if len(positive_pairs) > 0:
            pos_max = positive_pairs.max()
        else:
            pos_max = 0.0

        if len(negative_pairs) > 0:
            neg_min = negative_pairs.min()
        else:
            neg_min = 0.0

        loss = F.relu(pos_max - neg_min + adaptive_margin)
        return loss.mean()


class OnlineTripletLoss(nn.Module):
    def __init__(self, margin: float, random: bool = True):
        super().__init__()
        self.margin = margin
        self.random = random

    def forward(self, embeddings: torch.Tensor, labels: torch.Tensor) -> torch.Tensor:
        pairwise_dist = torch.cdist(embeddings, embeddings, p=2)

        anchor_idx, positive_idx, negative_idx = self._get_triplets(pairwise_dist, labels)

        if len(anchor_idx) == 0:
            return torch.tensor(0.0, device=embeddings.device)

        ap_dist = pairwise_dist[anchor_idx, positive_idx]
        an_dist = pairwise_dist[anchor_idx, negative_idx]

        loss = F.relu(ap_dist - an_dist + self.margin)
        return loss.mean()

    def _get_triplets(
        self, pairwise_dist: torch.Tensor, labels: torch.Tensor
    ) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        labels_equal = labels.unsqueeze(1) == labels.unsqueeze(0)
        labels_not_equal = ~labels_equal

        triplets = []

        for anchor_idx in range(labels.size(0)):
            positive_indices = torch.where(labels_equal[anchor_idx])[0]
            negative_indices = torch.where(labels_not_equal[anchor_idx])[0]

            if len(positive_indices) == 0 or len(negative_indices) == 0:
                continue

            for positive_idx in positive_indices:
                if anchor_idx == positive_idx:
                    continue

                for negative_idx in negative_indices:
                    ap = pairwise_dist[anchor_idx, positive_idx]
                    an = pairwise_dist[anchor_idx, negative_idx]
                    if an - ap > self.margin:
                        triplets.append((anchor_idx, positive_idx, negative_idx))

        if not triplets:
            return (
                torch.tensor([], device=labels.device, dtype=torch.long),
                torch.tensor([], device=labels.device, dtype=torch.long),
                torch.tensor([], device=labels.device, dtype=torch.long),
            )

        triplets = torch.tensor(triplets, device=labels.device)
        return triplets[:, 0], triplets[:, 1], triplets[:, 2]
