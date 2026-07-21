# RotBench fixtures

`fixtures.example.json` is a small golden-recall fixture file for `hsm verify --deep`.
In a real vault, put the file at:

```text
<vault>/.hsm/fixtures.json
```

The file is a JSON array. Each case has a natural-language `query` and an `expect`
value that is either the expected note stem or a relative path substring. During
deep verification, RotBench runs the query against the vault and emits
`fixture_miss` if the expected note is no longer retrieved.

Keep fixtures small and representative: use them for facts that must stay findable,
not for every note in the vault.
