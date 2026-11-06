"""A consistent light palette, including when the host requests dark mode."""

import gradio as gr


def application_theme():
    theme = gr.themes.Base(
        primary_hue="blue", neutral_hue="slate",
        font=["Segoe UI", "Arial", "sans-serif"], font_mono=["Consolas", "monospace"],
    ).set(
        body_background_fill="#ffffff", body_text_color="#172033",
        body_text_color_subdued="#475569", body_text_size="15px",
        background_fill_primary="#ffffff", background_fill_secondary="#f1f5f9",
        block_background_fill="#ffffff", block_border_color="#d5dde7",
        block_border_width="0px", block_padding="0px", block_shadow="none",
        block_label_background_fill="transparent", block_label_border_width="0px",
        block_label_text_color="#334155",
        block_label_text_size="14px", block_label_text_weight="600",
        block_title_text_color="#172033", block_title_text_size="15px",
        block_info_text_color="#475569", block_info_text_size="14px",
        input_background_fill="#ffffff", input_background_fill_focus="#ffffff",
        input_background_fill_hover="#ffffff", input_border_color="#b8c5d5",
        input_border_color_focus="#2563eb", input_border_width="1px", input_placeholder_color="#58677c",
        input_text_size="15px", input_padding="10px 12px", input_radius="6px",
        button_primary_background_fill="#1d4ed8", button_primary_background_fill_hover="#1e40af",
        button_primary_border_color="#1d4ed8", button_primary_text_color="#ffffff",
        button_secondary_background_fill="#f1f5f9", button_secondary_background_fill_hover="#e2e8f0",
        button_secondary_text_color="#172033", button_secondary_border_color="#cbd5e1",
        button_large_text_size="14px", button_large_padding="10px 16px",
        button_medium_text_size="14px", button_small_text_size="14px", button_transition="none",
    )
    # Gradio applies a .dark class independently of the application's canvas.
    # Mirror every dark token, rather than patching individual control colors.
    values = theme.to_dict()["theme"]
    return theme.set(**{key: values[key[:-5]] for key in values
                        if key.endswith("_dark") and key[:-5] in values})
