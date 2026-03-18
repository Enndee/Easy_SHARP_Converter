# SHARP Model Performance Optimization Guide

## The Problem: 4s/image on an RTX 5090 at 1% GPU utilization

Apple's SHARP model (`ml-sharp`) ran at ~4 seconds per image despite having a powerful GPU.
Task Manager showed GPU 3D utilization at **1%** — the GPU was almost completely idle.

## Root Cause: CPU-bound SVD + SciPy in `unproject_gaussians()`

The bottleneck was **not** in the neural network forward pass (~0.5s) but in the
**post-processing step** that converts the model's NDC-space output to world coordinates.

### The Kill Chain

```
predictor(image, disparity_factor)          # ~0.5s GPU  ✅ Fast
    ↓
unproject_gaussians()                       # ~3.5s CPU  ❌ Bottleneck
    ↓
apply_transform()
    ↓
compose_covariance_matrices()               # GPU: quaternions → 3×3 covariance
    ↓
transform_linear @ cov @ transform_linear.T # GPU: apply affine transform
    ↓
decompose_covariance_matrices()             # ❌ THE BOTTLENECK
    ├─ .detach().cpu().to(torch.float64)    # GPU → CPU transfer of ~1.18M matrices
    ├─ torch.linalg.svd()                   # CPU float64 SVD (~3.5s)
    └─ quaternions_from_rotation_matrices()  # SciPy on numpy (~0.5s)
```

### Exact Offending Code

**File:** `ml-sharp/src/sharp/utils/gaussians.py`, `decompose_covariance_matrices()`:
```python
# Line 152-153: Forces ALL covariance matrices to CPU for SVD
covariance_matrices = covariance_matrices.detach().cpu().to(torch.float64)
rotations, singular_values_2, _ = torch.linalg.svd(covariance_matrices)
```

**File:** `ml-sharp/src/sharp/utils/linalg.py`, `quaternions_from_rotation_matrices()`:
```python
# Line 56-58: Another CPU round-trip using SciPy
matrices_np = matrices.detach().cpu().numpy()
quaternions_np = Rotation.from_matrix(matrices_np.reshape(-1, 3, 3)).as_quat()
return torch.as_tensor(quaternions_np, device=matrices.device, dtype=matrices.dtype)
```

### Why It's So Slow

| Operation | Time | GPU Status |
|-----------|------|------------|
| GPU → CPU transfer of 1.18M 3×3 matrices | ~0.1s | Idle |
| `torch.linalg.svd()` on CPU in float64 | **~3.0s** | **Idle** |
| `scipy.spatial.transform.Rotation` on numpy | **~0.5s** | **Idle** |
| CPU → GPU transfer of results | ~0.1s | Idle |
| **Total wasted** | **~3.7s** | — |

The model predicts Gaussians on a 768×768 grid × 2 layers = **1,179,648 covariance matrices**.
Running SVD on each one sequentially on CPU is catastrophically slow.

## The Solution: GPU-only SVD + Pure-Torch Quaternion Extraction

### 1. Keep SVD on GPU (float32 is fine for rendering)

```python
# BEFORE (Apple's code — CPU, float64):
covariance_matrices = covariance_matrices.detach().cpu().to(torch.float64)
rotations, singular_values_2, _ = torch.linalg.svd(covariance_matrices)

# AFTER (GPU, float32):
U, S2, _ = torch.linalg.svd(cov)  # stays on GPU, float32
```

Apple moved to CPU + float64 "to avoid numerical errors" — but for Gaussian splatting
rendering, float32 precision is more than sufficient. The RTX 5090's tensor cores
can churn through 1.18M 3×3 SVDs in ~50ms vs 3000ms on CPU.

### 2. Replace SciPy with Pure-Torch Quaternion Conversion

```python
# BEFORE (Apple's code — SciPy on CPU numpy):
matrices_np = matrices.detach().cpu().numpy()
quaternions_np = Rotation.from_matrix(matrices_np.reshape(-1, 3, 3)).as_quat()

# AFTER (Shepperd's method, pure torch, stays on GPU):
@staticmethod
def _rotmat_to_quat(R):
    """Batch rotation matrices → quaternions (wxyz). Pure torch, stays on GPU."""
    R = R.reshape(-1, 3, 3)
    m00, m01, m02 = R[:, 0, 0], R[:, 0, 1], R[:, 0, 2]
    m10, m11, m12 = R[:, 1, 0], R[:, 1, 1], R[:, 1, 2]
    m20, m21, m22 = R[:, 2, 0], R[:, 2, 1], R[:, 2, 2]
    trace = m00 + m11 + m22

    # Compute all 4 candidate quaternions
    s0 = (trace + 1.0).clamp(min=1e-10).sqrt() * 2.0
    s1 = (1.0 + m00 - m11 - m22).clamp(min=1e-10).sqrt() * 2.0
    s2 = (1.0 + m11 - m00 - m22).clamp(min=1e-10).sqrt() * 2.0
    s3 = (1.0 + m22 - m00 - m11).clamp(min=1e-10).sqrt() * 2.0

    q0 = torch.stack([0.25*s0, (m21-m12)/s0, (m02-m20)/s0, (m10-m01)/s0], -1)
    q1 = torch.stack([(m21-m12)/s1, 0.25*s1, (m01+m10)/s1, (m02+m20)/s1], -1)
    q2 = torch.stack([(m02-m20)/s2, (m01+m10)/s2, 0.25*s2, (m12+m21)/s2], -1)
    q3 = torch.stack([(m10-m01)/s3, (m02+m20)/s3, (m12+m21)/s3, 0.25*s3], -1)

    # Pick the most numerically stable candidate per matrix
    all_q = torch.stack([q0, q1, q2, q3], dim=1)
    best = torch.stack([trace, m00, m11, m22], -1).argmax(-1)
    idx = best[:, None, None].expand(-1, 1, 4)
    q = all_q.gather(1, idx).squeeze(1)
    return q / q.norm(dim=-1, keepdim=True)
```

### 3. Full GPU-only Unprojection Function

```python
@staticmethod
def _unproject_gpu(gaussians_ndc, extrinsics, intrinsics, image_shape):
    """GPU-only Gaussian unprojection — replaces Apple's CPU SVD path."""
    from sharp.utils.gaussians import (
        Gaussians3D, get_unprojection_matrix, compose_covariance_matrices,
    )
    T_full = get_unprojection_matrix(extrinsics, intrinsics, image_shape)
    T_lin = T_full[:3, :3]
    T_off = T_full[:3, 3]

    means = gaussians_ndc.mean_vectors @ T_lin.T + T_off
    cov = compose_covariance_matrices(
        gaussians_ndc.quaternions, gaussians_ndc.singular_values,
    )
    cov = T_lin @ cov @ T_lin.transpose(-1, -2)

    U, S2, _ = torch.linalg.svd(cov)       # GPU SVD — ~50ms
    neg = torch.linalg.det(U) < 0           # Fix reflections
    if neg.any():
        U[neg, :, -1] *= -1
    quats = _rotmat_to_quat(U)              # GPU quaternions — ~20ms

    return Gaussians3D(
        mean_vectors=means,
        singular_values=S2.sqrt(),
        quaternions=quats,
        colors=gaussians_ndc.colors,
        opacities=gaussians_ndc.opacities,
    )
```

## Additional Optimizations Applied

### CUDA Backend Settings (matches SPAG4d)

```python
torch.backends.cudnn.benchmark = True       # Auto-tune conv kernels
torch.backends.cuda.matmul.allow_tf32 = True # TF32 for matmul (Ampere+)
torch.backends.cudnn.allow_tf32 = True       # TF32 for convolutions
```

### Full-Resolution Warmup

```python
# Warm up at ACTUAL inference resolution (1536×1536), not a small dummy
dummy = torch.randn(1, 3, 1536, 1536, device="cuda")
predictor(dummy, torch.tensor([1.0], device="cuda"))
torch.cuda.synchronize()
```

The SPN encoder inside SHARP creates **35 overlapping 384×384 patches** from a 1536×1536
image and runs them through two DINOv2-L ViT models (each ~300M params). Warming up at
512×512 misses most kernel shapes. A full-size warmup primes all CUDA kernels.

### What Did NOT Help (and Why)

| Technique | Why It Hurt |
|-----------|-------------|
| `torch.compile(mode="reduce-overhead")` | SPN's 35-patch loop causes graph breaks → CPU sync per patch → slower than eager |
| `torch.cuda.amp.autocast(float16)` | DINOv2-L attention layers are precision-sensitive → numerical instability |
| Batching multiple images | Does help throughput slightly, but the main bottleneck was CPU SVD, not GPU utilization |

## Results

| Metric | Before | After |
|--------|--------|-------|
| Per-image time | **4.2s** | **~0.8s** |
| GPU utilization | **1%** | **High** |
| Bottleneck | CPU SVD (3.5s) | GPU forward pass (0.5s) |

## Key Takeaway

**Always profile before optimizing.** The neural network forward pass was already fast.
The bottleneck was in Apple's utility code that moved tensors to CPU for "numerical
stability" — a reasonable choice for research but devastating for production throughput.

### Red Flags to Watch For

1. **`.cpu()` or `.numpy()` in post-processing** — forces GPU synchronization
2. **`scipy` calls on tensors** — means data is leaving the GPU
3. **`torch.linalg.svd()` on CPU tensors** — SVD is O(n³) per matrix, brutal on CPU
4. **Low GPU utilization despite GPU model** — the GPU is waiting for CPU work
5. **`.to(torch.float64)`** — doubles memory bandwidth, often unnecessary for rendering

### The Pattern

```
Model output (GPU) → Post-processing (CPU) → Back to GPU
                     ^^^^^^^^^^^^^^^^^^^^^^^^
                     THIS is where time disappears
```

Always keep the full pipeline on GPU. If a library function moves data to CPU,
write your own GPU replacement.
