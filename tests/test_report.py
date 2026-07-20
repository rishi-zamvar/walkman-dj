from walkman_dj.report import build_report
def test_report_has_counts_and_arcs():
    stats={"analyzed":3200,"failed":12,
           "playlists":[{"name":"Opium Rage","arc":"hype","count":48},
                        {"name":"Alt-R&B","arc":"soul","count":60}],
           "bpm_hist":{"120-129":800,"130-139":400},
           "camelot_hist":{"8A":120,"9A":90}}
    md=build_report(stats)
    assert "3200" in md and "Opium Rage" in md and "hype" in md and "8A" in md
