import pytest

from aphrodite.worker.model_runner import _get_graph_batch_size

from ...utils import check_outputs_equal

MODELS = ["ai21labs/Jamba-tiny-random"]


# Fails due to usage of MoE as MLP(E=1_, which is different than the HF impl
# TODO: Fix this with trained model
@pytest.mark.skip()
@pytest.mark.parametrize("model", MODELS)
@pytest.mark.parametrize("dtype", ["bfloat16"])
@pytest.mark.parametrize("max_tokens", [10])
def test_models(
    hf_runner,
    aphrodite_runner,
    example_prompts,
    model: str,
    dtype: str,
    max_tokens: int,
) -> None:

    with hf_runner(model, dtype=dtype) as hf_model:
        hf_outputs = hf_model.generate_greedy(example_prompts, max_tokens)

    with aphrodite_runner(model, dtype=dtype) as aphrodite_model:
        aphrodite_outputs = aphrodite_model.generate_greedy(
            example_prompts, max_tokens)

    for i in range(len(example_prompts)):
        hf_output_ids, hf_output_str = hf_outputs[i]
        aphrodite_output_ids, aphrodite_output_str = aphrodite_outputs[i]
        assert hf_output_str == aphrodite_output_str, (
            f"Test{i}:\nHF: {hf_output_str!r}\nAPHRODITE: "
            f"{aphrodite_output_str!r}")
        assert hf_output_ids == aphrodite_output_ids, (
            f"Test{i}:\nHF: {hf_output_ids}\nAPHRODITE: "
            f"{aphrodite_output_ids}")


@pytest.mark.parametrize("model", MODELS)
@pytest.mark.parametrize("dtype", ["half"])
@pytest.mark.parametrize("max_tokens", [5])
def test_batching(
    aphrodite_runner,
    example_prompts,
    model: str,
    dtype: str,
    max_tokens: int,
) -> None:
    # To pass the small model tests, we need full precision.
    for_loop_outputs = []
    with aphrodite_runner(model, dtype=dtype) as aphrodite_model:
        for prompt in example_prompts:
            for_loop_outputs.append(
                aphrodite_model.generate_greedy([prompt], max_tokens)[0])

        batched_outputs = aphrodite_model.generate_greedy(example_prompts,
                                                     max_tokens)

    check_outputs_equal(
        outputs_0_lst=for_loop_outputs,
        outputs_1_lst=batched_outputs,
        name_0="for_loop_aphrodite",
        name_1="batched_aphrodite",
    )


@pytest.mark.parametrize("model", MODELS)
@pytest.mark.parametrize("dtype", ["bfloat16"])
@pytest.mark.parametrize("max_tokens", [20])
def test_mamba_cache_cg_padding(
    aphrodite_runner,
    example_prompts,
    model: str,
    dtype: str,
    max_tokens: int,
) -> None:
    # This test is for verifying that mamba cache is padded to CG captured
    # batch size. If it's not, a torch RuntimeError will be raised because
    # tensor dimensions aren't compatible
    while len(example_prompts) == _get_graph_batch_size(len(example_prompts)):
        example_prompts.append(example_prompts[0])

    try:
        with aphrodite_runner(model, dtype=dtype) as aphrodite_model:
            aphrodite_model.generate_greedy(example_prompts, max_tokens)
    except RuntimeError:
        pytest.fail(
            "Couldn't run batch size which is not equal to a Cuda Graph "
            "captured batch size. "
            "Could be related to mamba cache not padded correctly")


@pytest.mark.parametrize("model", MODELS)
@pytest.mark.parametrize("dtype", ["float"])
@pytest.mark.parametrize("max_tokens", [20])
def test_models_preemption_recompute(
    hf_runner,
    aphrodite_runner,
    example_prompts,
    model: str,
    dtype: str,
    max_tokens: int,
) -> None:
    # Tests that outputs are identical with and w/o preemtions (recompute)
    assert dtype == "float"

    with aphrodite_runner(model, dtype=dtype) as aphrodite_model:
        aphrodite_model.model.llm_engine.scheduler[
            0].ENABLE_ARTIFICIAL_PREEMPT = True
        preempt_aphrodite_outputs = aphrodite_model.generate_greedy(
            example_prompts, max_tokens)

        aphrodite_model.model.llm_engine.scheduler[
            0].ENABLE_ARTIFICIAL_PREEMPT = False
        aphrodite_outputs = aphrodite_model.generate_greedy(
            example_prompts, max_tokens)

    check_outputs_equal(
        outputs_0_lst=preempt_aphrodite_outputs,
        outputs_1_lst=aphrodite_outputs,
        name_0="aphrodite_preepmtions",
        name_1="aphrodite",
    )


@pytest.mark.parametrize("model", MODELS)
@pytest.mark.parametrize("dtype", ["float"])
def test_fail_upon_inc_requests_and_finished_requests_lt_available_blocks(
    aphrodite_runner,
    model: str,
    dtype: str,
    example_prompts,
) -> None:
    # This test is for verifying that the Jamba inner state management doesn't
    # collapse in case where the number of incoming requests and
    # finished_requests_ids is larger than the maximum mamba block capacity.
    # This could generally happen due to the fact that Jamba does support
    # statelessness mechanism where it can cleanup new incoming requests in
    # a single step.
    try:
        with aphrodite_runner(
            model,
            dtype=dtype,
            max_num_seqs=10,
        ) as aphrodite_model:
            aphrodite_model.generate_greedy([example_prompts[0]] * 100, 10)
    except ValueError:
        pytest.fail("Jamba inner state wasn't cleaned up properly between"
                    "steps finished requests registered unnecessarily ")


@pytest.mark.parametrize("model", MODELS)
@pytest.mark.parametrize("dtype", ["float"])
def test_state_cleanup(
    aphrodite_runner,
    model: str,
    dtype: str,
    example_prompts,
) -> None:
    # This test is for verifying that the Jamba state is cleaned up between
    # steps, If its not cleaned, an error would be expected.
    try:
        with aphrodite_runner(model, dtype=dtype) as aphrodite_model:
            for _ in range(10):
                aphrodite_model.generate_greedy([example_prompts[0]] * 100, 1)
    except ValueError:
        pytest.fail("Jamba inner state wasn't cleaned up between states, "
                    "could be related to finished_requests_ids")


@pytest.mark.parametrize("model", MODELS)
@pytest.mark.parametrize("dtype", ["float"])
def test_model_print(
    aphrodite_runner,
    model: str,
    dtype: str,
) -> None:
    with aphrodite_runner(model, dtype=dtype) as aphrodite_model:
        # This test is for verifying whether the model's extra_repr
        # can be printed correctly.
        print(aphrodite_model.model.llm_engine.model_executor.driver_worker.
              model_runner.model)
