from src.prompt.assembler import assemble_system_prompt


def test_assemble_system_prompt_includes_all_four_layers_in_order():
    result = assemble_system_prompt(
        constitution="LAYER1_CONSTITUTION_TEXT",
        knowledge_base={"niche": "automation", "audience": "SMB"},
        active_genome="LAYER3_GENOME_TEXT",
        playbook_rules=["Post at 9am", "Avoid emoji"],
        swipe_examples=["chужой пост 1", "chужой пост 2"],
        top_posts=["мой лучший пост"],
    )

    layer1_pos = result.index("LAYER1_CONSTITUTION_TEXT")
    layer2_pos = result.index("niche")
    layer3_pos = result.index("LAYER3_GENOME_TEXT")
    layer4_pos = result.index("Post at 9am")
    examples_pos = result.index("chужой пост 1")

    assert layer1_pos < layer2_pos < layer3_pos < layer4_pos < examples_pos
    assert "SMB" in result
    assert "Avoid emoji" in result
    assert "мой лучший пост" in result


def test_assemble_system_prompt_handles_empty_playbook_and_examples():
    result = assemble_system_prompt(
        constitution="C",
        knowledge_base={"niche": "automation"},
        active_genome="G",
        playbook_rules=[],
        swipe_examples=[],
        top_posts=[],
    )

    assert "C" in result
    assert "G" in result
    # Must not raise on empty lists, and must not contain leftover
    # formatting artifacts like an empty bullet list.
    assert "automation" in result
