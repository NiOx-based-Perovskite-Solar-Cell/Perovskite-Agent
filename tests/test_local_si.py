from psc_agent.sources.pairing import local_si


def test_local_si_finds_only_this_papers_files(tmp_path):
    (tmp_path / "SI").mkdir()
    for name in ("101039d3lf00258f_SI.pdf", "other_SI.pdf"):
        (tmp_path / "SI" / name).write_bytes(b"%PDF")
    assert [p.name for p in local_si(tmp_path, "101039d3lf00258f")] == ["101039d3lf00258f_SI.pdf"]
    assert local_si(tmp_path / "nope", "x") == []
