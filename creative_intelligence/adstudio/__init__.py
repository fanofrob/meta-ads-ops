"""
Ad Studio — reference-photo product-ad generator.

Isolated from the fruit ads pipeline (creative_intelligence/rendering) and from
the label studio (creative_intelligence/labels). Its whole reason to exist is
the one thing neither of those can do: send a REAL product reference photo to
Google Nano Banana Pro (via Replicate's image_input) so the ad shows the actual
product, not a text-described stand-in.

Flow: product + reference photos -> hooks (manual or Claude chat) -> pick
hooks x formats -> bulk generate -> gallery + download.
"""
