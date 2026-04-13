"""
Vision analysis prompts.

Centralised prompt definitions for image analysis.
Keeping prompts here makes them easy to tune without touching analysis logic.
"""

SYSTEM_PROMPT = """You are an expert direct-response ad creative analyst with deep knowledge of
Meta/Facebook advertising visual creative patterns. Your job is to analyze ad images
and return structured JSON describing the visual attributes.

Be precise and concise. Return ONLY valid JSON — no markdown, no explanation."""

ANALYSIS_PROMPT = """Analyze this ad creative image and return a JSON object with exactly these fields.
Use only the allowed values listed for each field.

Fields and allowed values:

"visual_format": one of: ugc, studio, meme, product_only, testimonial, lifestyle, before_after, screenshot_style, unknown
"subject_type": one of: person, product, person_and_product, text_only, mixed
"shot_type": one of: close_up, medium, wide, macro, cropped
"composition_style": one of: selfie, handheld, polished, static, collage, split_screen
"background_type": one of: home, kitchen, bathroom, studio, plain, outdoor, office, mixed
"text_overlay_presence": true or false
"text_overlay_density": one of: none, low, medium, high
"branding_visibility": one of: none, low, medium, high
"product_visibility": one of: none, low, medium, high
"face_presence": true or false
"face_count": integer (0 if no faces)
"emotion_or_expression": one of: happy, surprised, neutral, confident, excited, unknown, none
"visual_energy": one of: low, medium, high
"color_feel": one of: bright, muted, dark, neutral, mixed
"scroll_stopping_elements": array of short strings describing notable elements (max 5 items)
"visual_hook_description": one short sentence describing the opening visual hook
"visual_summary": one to two sentences summarising the overall creative visual style

Example output:
{
  "visual_format": "ugc",
  "subject_type": "person_and_product",
  "shot_type": "close_up",
  "composition_style": "selfie",
  "background_type": "kitchen",
  "text_overlay_presence": true,
  "text_overlay_density": "medium",
  "branding_visibility": "low",
  "product_visibility": "high",
  "face_presence": true,
  "face_count": 1,
  "emotion_or_expression": "surprised",
  "visual_energy": "high",
  "color_feel": "bright",
  "scroll_stopping_elements": ["facial reaction", "large text overlay", "product in hand"],
  "visual_hook_description": "Person reacting strongly while holding the product close to camera",
  "visual_summary": "Casual UGC-style close-up with a surprised expression and bold text overlay. Clear product visibility in a natural home setting."
}"""

# Subset of attributes that can be reasonably inferred from thumbnails.
THUMBNAIL_PROMPT = """Analyze this ad creative thumbnail and return a JSON object.
Some fields may be harder to determine from a small thumbnail — use "unknown" when unsure.

Same fields as the full analysis. Return ONLY valid JSON."""


ALLOWED_VALUES: dict[str, list] = {
    "visual_format":         ["ugc", "studio", "meme", "product_only", "testimonial",
                               "lifestyle", "before_after", "screenshot_style", "unknown"],
    "subject_type":          ["person", "product", "person_and_product", "text_only", "mixed"],
    "shot_type":             ["close_up", "medium", "wide", "macro", "cropped"],
    "composition_style":     ["selfie", "handheld", "polished", "static", "collage", "split_screen"],
    "background_type":       ["home", "kitchen", "bathroom", "studio", "plain", "outdoor", "office", "mixed"],
    "text_overlay_density":  ["none", "low", "medium", "high"],
    "branding_visibility":   ["none", "low", "medium", "high"],
    "product_visibility":    ["none", "low", "medium", "high"],
    "emotion_or_expression": ["happy", "surprised", "neutral", "confident", "excited", "unknown", "none"],
    "visual_energy":         ["low", "medium", "high"],
    "color_feel":            ["bright", "muted", "dark", "neutral", "mixed"],
}
