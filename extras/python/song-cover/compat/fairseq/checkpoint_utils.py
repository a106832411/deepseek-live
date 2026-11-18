"""Minimal fairseq shim: only what rvc-python needs (checkpoint_utils.load_model_ensemble_and_task).

Builds a torchaudio HuBERT-Base model and loads the fairseq `hubert_base.pt`
weights into it, so RVC feature extraction works without the fairseq package
(which has no cp312 wheels).
"""
import pickle
import re
import types

import torch
from collections import OrderedDict

__all__ = ["checkpoint_utils", "HubertShim"]


class _Ph:
    def __init__(self, *a, **k):
        self._args = a
        self._kw = k

    def __repr__(self):
        return f"<Ph {self.__class__.__name__}>"


class _Unpickler(pickle.Unpickler):
    def find_class(self, mod, name):
        if mod.split(".")[0] in ("fairseq", "omegaconf", "hydra"):
            return type(name, (_Ph,), {})
        return super().find_class(mod, name)


def _load_ckpt(path, map_location="cpu"):
    mod = types.SimpleNamespace(
        Unpickler=_Unpickler, load=pickle.load, loads=pickle.loads
    )
    mod.__name__ = "shimpickle"
    try:
        return torch.load(path, map_location=map_location, weights_only=False,
                          pickle_module=mod)
    except TypeError:
        return torch.load(path, map_location=map_location, pickle_module=mod)


def _conv(n):
    return f"feature_extractor.conv_layers.{n}"


def _map_key(k):
    m = re.match(r"^feature_extractor\.conv_layers\.(\d+)\.0\.weight$", k)
    if m:
        return f"{_conv(m.group(1))}.conv.weight"
    m = re.match(r"^feature_extractor\.conv_layers\.0\.2\.(weight|bias)$", k)
    if m:
        return f"{_conv(0)}.layer_norm.{m.group(1)}"
    if k in ("post_extract_proj.weight", "post_extract_proj.bias"):
        return "encoder.feature_projection.projection." + k.split(".")[-1]
    if k in ("layer_norm.weight", "layer_norm.bias"):
        return "encoder.feature_projection.layer_norm." + k.split(".")[-1]
    if k == "encoder.pos_conv.0.bias":
        return "encoder.transformer.pos_conv_embed.conv.bias"
    if k == "encoder.pos_conv.0.weight_g":
        return "encoder.transformer.pos_conv_embed.conv.parametrizations.weight.original0"
    if k == "encoder.pos_conv.0.weight_v":
        return "encoder.transformer.pos_conv_embed.conv.parametrizations.weight.original1"
    m = re.match(
        r"^encoder\.layers\.(\d+)\.self_attn\.(k_proj|v_proj|q_proj|out_proj)\.(weight|bias)$",
        k,
    )
    if m:
        return f"encoder.transformer.layers.{m.group(1)}.attention.{m.group(2)}.{m.group(3)}"
    m = re.match(r"^encoder\.layers\.(\d+)\.self_attn_layer_norm\.(weight|bias)$", k)
    if m:
        return f"encoder.transformer.layers.{m.group(1)}.layer_norm.{m.group(2)}"
    m = re.match(r"^encoder\.layers\.(\d+)\.fc1\.(weight|bias)$", k)
    if m:
        return f"encoder.transformer.layers.{m.group(1)}.feed_forward.intermediate_dense.{m.group(2)}"
    m = re.match(r"^encoder\.layers\.(\d+)\.fc2\.(weight|bias)$", k)
    if m:
        return f"encoder.transformer.layers.{m.group(1)}.feed_forward.output_dense.{m.group(2)}"
    m = re.match(r"^encoder\.layers\.(\d+)\.final_layer_norm\.(weight|bias)$", k)
    if m:
        return f"encoder.transformer.layers.{m.group(1)}.final_layer_norm.{m.group(2)}"
    if k in ("encoder.layer_norm.weight", "encoder.layer_norm.bias"):
        return "encoder.transformer.layer_norm." + k.split(".")[-1]
    return None  # drop (e.g. mask_emb, final_proj, quantizer.*)


class HubertShim(torch.nn.Module):
    """Drop-in replacement for fairseq's HubertModel used by RVC pipeline."""

    def __init__(self, state_dict):
        super().__init__()
        from torchaudio.models import hubert_base

        self.model = hubert_base()
        mapped = OrderedDict()
        skipped = []
        for k, v in state_dict.items():
            nk = _map_key(k)
            if nk is not None:
                mapped[nk] = v
            else:
                skipped.append(k)
        missing, unexpected = self.model.load_state_dict(mapped, strict=False)
        self._meta = {"skipped": skipped, "missing": list(missing),
                      "unexpected": list(unexpected)}
        self.model.eval()

    def extract_features(self, source=None, padding_mask=None, output_layer=12,
                         **kwargs):
        if source is None:
            source = kwargs.get("source")
        b, t = source.shape
        lengths = torch.full((b,), t, dtype=torch.long, device=source.device)
        layers, _ = self.model.extract_features(source, lengths=lengths)
        n = int(output_layer) if output_layer else len(layers)
        idx = max(1, min(n, len(layers))) - 1
        return (layers[idx], padding_mask)

    def forward(self, *args, **kwargs):
        return self.extract_features(*args, **kwargs)


def load_hubert_model(path, device="cpu"):
    ck = _load_ckpt(path)
    if isinstance(ck, dict):
        sd = ck.get("model", ck)
    else:
        sd = ck
    model = HubertShim(sd)
    return model.to(device).float().eval()


def load_model_ensemble_and_task(paths, suffix="", **kwargs):
    models = []
    for p in paths:
        models.append(load_hubert_model(p))
    return models, None, None
