"""
Production handoff module.

Turns approved copilot concepts into structured production-ready documents
that can be handed directly to marketers, designers, and UGC creators.

Public API
----------
brief_builder.build_static_brief(concept, product_id, conn, ...)
brief_builder.build_ugc_brief(concept, product_id, conn, ...)
brief_builder.build_script_package(concept, product_id, conn, ...)
brief_builder.build_test_package(concept, product_id, session_id, conn, ...)

exporters.to_markdown(output_type, data) → str
exporters.to_json(data) → str
exporters.to_text_block(output_type, data) → str
"""
