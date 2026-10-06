# Template: extraction / classification to JSON

- **purpose:** extract structured fields from a supplied document, or classify
  it against a fixed label set, returning one JSON object.
- **output contract:** one JSON object with the declared fields/labels;
  unknown or absent values use explicit nulls — never invented content.
- **validated against:** no measured run yet.
- **measured result:** none — shape follows the measured JSON-contract
  pattern.
- **status:** UNVALIDATED.

## Prompt shape

```text
You are the EXTRACT worker. From the document between the markers, extract
<field list> and return EXACTLY ONE JSON object (no prose, no markdown
fences): {"field_a": <...>, "label": <one of ["A", "B", "C"]>}.
DO NOT call any tools. Tool use terminates the task.
Rules: every value must come from the document — if a field is absent or
ambiguous, use null; "label" must be one of the listed labels verbatim; do
not summarize, paraphrase, or add fields.
--- DOCUMENT ---
<the complete document>
--- END DOCUMENT ---
```

```json
{
  "field_a": null,
  "label": "A"
}
```

## Notes

- The null-on-absence rule is the important one: without it, small models
  fabricate plausible values for missing fields, and a downstream gate that
  trusts the JSON inherits the fabrication.
- For classification, list the label set inline and require verbatim
  membership — a free-text "label-ish" field defeats caller-side validation.
