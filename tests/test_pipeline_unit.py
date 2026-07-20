from walkman_dj.pipeline import plan_batches, pending_only, bpm_band, safe_name

def test_plan_batches_chunks():
    items=list(range(350))
    assert [len(b) for b in plan_batches(items,150)]==[150,150,50]

def test_pending_only_skips_done():
    class FakeDB:
        def needs_analysis(self,fp): return fp!="done"
    F=type("F",(),{})
    f=F(); f.path="a"; f.filename="a"
    got=pending_only([f], FakeDB(), fp_of=lambda x:"done")
    assert got==[]

def test_bpm_band():
    assert bpm_band(126)=="124-127"
    assert bpm_band(130)=="128-131"

def test_safe_name_strips_bad_chars():
    assert safe_name("Opium Rage — Hard/2")=="Opium Rage _ Hard_2"
