import os
import warnings
from pathlib import Path
from typing import Optional, Union

import huggingface_hub
from loguru import logger
from transformers import (AutoTokenizer, PreTrainedTokenizer,
                          PreTrainedTokenizerFast)

from aphrodite.common.envs import APHRODITE_USE_MODELSCOPE
from aphrodite.common.utils import make_async
from aphrodite.lora.request import LoRARequest
from aphrodite.transformers_utils.tokenizers import (BaichuanTokenizer,
                                                     MistralTokenizer)
from aphrodite.transformers_utils.utils import check_gguf_file

AnyTokenizer = Union[PreTrainedTokenizer, PreTrainedTokenizerFast,
                     MistralTokenizer]


def get_cached_tokenizer(tokenizer: AnyTokenizer) -> AnyTokenizer:
    """Get tokenizer with cached properties.

    This will patch the tokenizer object in place.

    By default, transformers will recompute multiple tokenizer properties
    each time they are called, leading to a significant slowdown. This
    function caches these properties for faster access."""

    tokenizer_all_special_ids = set(tokenizer.all_special_ids)
    tokenizer_all_special_tokens_extended = (
        tokenizer.all_special_tokens_extended)
    tokenizer_all_special_tokens = set(tokenizer.all_special_tokens)
    tokenizer_len = len(tokenizer)

    class CachedTokenizer(tokenizer.__class__):  # type: ignore

        @property
        def all_special_ids(self):
            return tokenizer_all_special_ids

        @property
        def all_special_tokens(self):
            return tokenizer_all_special_tokens

        @property
        def all_special_tokens_extended(self):
            return tokenizer_all_special_tokens_extended

        def __len__(self):
            return tokenizer_len

    CachedTokenizer.__name__ = f"Cached{tokenizer.__class__.__name__}"

    tokenizer.__class__ = CachedTokenizer
    return tokenizer


def get_tokenizer(
    tokenizer_name: Union[str, Path],
    *args,
    tokenizer_mode: str = "auto",
    trust_remote_code: bool = False,
    revision: Optional[str] = None,
    download_dir: Optional[str] = None,
    **kwargs,
) -> AnyTokenizer:
    """Gets a tokenizer for the given model name via HuggingFace or ModelScope.
    """
    if APHRODITE_USE_MODELSCOPE:
        # download model from ModelScope hub,
        # lazy import so that modelscope is not required for normal use.
        # pylint: disable=C.
        from modelscope.hub.snapshot_download import snapshot_download

        # Only set the tokenizer here, model will be downloaded on the workers.
        if not os.path.exists(tokenizer_name):
            tokenizer_path = snapshot_download(
                model_id=tokenizer_name,
                cache_dir=download_dir,
                revision=revision,
                local_files_only=huggingface_hub.constants.HF_HUB_OFFLINE,
                # Ignore weights - we only need the tokenizer.
                ignore_file_pattern=[".*.pt", ".*.safetensors", ".*.bin"])
            tokenizer_name = tokenizer_path

    if tokenizer_mode == "slow":
        if kwargs.get("use_fast", False):
            raise ValueError(
                "Cannot use the fast tokenizer in slow tokenizer mode.")
        kwargs["use_fast"] = False

    if "truncation_side" not in kwargs:
        kwargs["truncation_side"] = "left"

    # Separate model folder from file path for GGUF models
    is_gguf = check_gguf_file(tokenizer_name)
    if is_gguf:
        kwargs["gguf_file"] = Path(tokenizer_name).name
        tokenizer_name = Path(tokenizer_name).parent

    # if tokenizer is from official mistral org
    is_from_mistral_org = str(tokenizer_name).split("/")[0] == "mistralai"
    if is_from_mistral_org and tokenizer_mode != "mistral":
        warnings.warn(
            'It is strongly recommended to run mistral models with '
            '`--tokenizer_mode "mistral"` to ensure correct '
            'encoding and decoding.',
            FutureWarning,
            stacklevel=2)

    if tokenizer_mode == "mistral":
        tokenizer = MistralTokenizer.from_pretrained(str(tokenizer_name),
                                                     revision=revision)
    else:
        try:
            tokenizer = AutoTokenizer.from_pretrained(
                tokenizer_name,
                *args,
                trust_remote_code=trust_remote_code,
                revision=revision,
                **kwargs,
            )
        except ValueError as e:
            # If the error pertains to the tokenizer class not existing or not
            # currently being imported,
            # suggest using the --trust-remote-code flag.
            if not trust_remote_code and (
                    "does not exist or is not currently imported." in str(e)
                    or "requires you to execute the tokenizer file" in str(e)):
                err_msg = ("Failed to load the tokenizer. If the tokenizer "
                           "is a custom tokenizer not yet available in the "
                           "HuggingFace transformers library, consider "
                           "setting `trust_remote_code=True` in LLM or using "
                           "the `--trust-remote-code` flag in the CLI.")
                raise RuntimeError(err_msg) from e
            else:
                raise e
        except AttributeError as e:
            if "BaichuanTokenizer" in str(e):
                # This is for the error "'BaichuanTokenizer' object has no
                # attribute 'sp_model'".
                tokenizer = BaichuanTokenizer.from_pretrained(
                    tokenizer_name,
                    *args,
                    trust_remote_code=trust_remote_code,
                    revision=revision,
                    **kwargs,
                )
            else:
                raise e

        if not isinstance(tokenizer, PreTrainedTokenizerFast):
            logger.warning(
                "Using a slow tokenizer. This might cause a significant "
                "slowdown. Consider using a fast tokenizer instead.")
        tokenizer = get_cached_tokenizer(tokenizer)

    return tokenizer


def get_lora_tokenizer(lora_request: LoRARequest, *args,
                       **kwargs) -> Optional[AnyTokenizer]:
    if lora_request is None:
        return None
    try:
        tokenizer = get_tokenizer(lora_request.lora_path, *args, **kwargs)
    except OSError as e:
        # No tokenizer was found in the LoRA folder,
        # use base model tokenizer
        logger.warning(f"No tokenizer found in {lora_request.lora_path}, "
                       "using base model tokenizer instead. "
                       f"(Exception: {str(e)})")
        tokenizer = None
    return tokenizer


get_lora_tokenizer_async = make_async(get_lora_tokenizer)
