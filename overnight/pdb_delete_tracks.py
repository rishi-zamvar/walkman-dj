#!/usr/bin/env python3
"""Surgically mark tracks as deleted in a Rekordbox DeviceSQL export.pdb by clearing
their row-presence bits — the same mechanism Rekordbox itself uses. Every other track
row (and its ANLZ links, cues, beatgrids) is left byte-identical. Does NOT rewrite the
file structure, so it's low-risk. Validate the output with `rekordcrate dump-pdb`.

Track rows are identified by matching a filename (basename) that appears (UTF-16-LE)
inside the row's heap allocation; the owning row is the present track row whose start
(row_base) is the greatest offset <= the match. page_type tracks=0, playlist_entries=8.

Usage: pdb_delete_tracks.py IN.pdb OUT.pdb names.txt   (names.txt = one basename per line)
"""
import sys


def u2(b, o):
    return b[o] | (b[o + 1] << 8)


def u4(b, o):
    return int.from_bytes(b[o:o + 4], "little")


def page_rows(data, len_page, pb):
    """(R, present, row_base_abs) for every row-index slot on a data page."""
    if (data[pb + 0x1b] & 0x40) != 0:              # not a data page
        return []
    nrs = data[pb + 0x18]
    nrl = u2(data, pb + 0x22)
    num_rows = nrl if (nrl > nrs and nrl != 0x1fff) else nrs
    if num_rows == 0:
        return []
    ngroups = (num_rows - 1) // 16 + 1
    out = []
    for g in range(ngroups):
        gbase = len_page - g * 0x24                 # offset within page (builds backward)
        flags = u2(data, pb + gbase - 4)
        n_in = 16 if g < ngroups - 1 else ((num_rows - 1) % 16 + 1)
        for i in range(n_in):
            ofs = u2(data, pb + gbase - (6 + 2 * i))
            out.append((g * 16 + i, bool((flags >> i) & 1), pb + 0x28 + ofs))
    return out


def clear_present(data, len_page, pb, R):
    g, i = R // 16, R % 16
    off = pb + (len_page - g * 0x24) - 4
    flags = u2(data, off) & ~(1 << i)
    data[off] = flags & 0xFF
    data[off + 1] = (flags >> 8) & 0xFF


def main():
    inp, outp, namesf = sys.argv[1], sys.argv[2], sys.argv[3]
    data = bytearray(open(inp, "rb").read())
    len_page = u4(data, 4)
    npages = len(data) // len_page
    names = [l.strip() for l in open(namesf, encoding="utf-8") if l.strip()]

    # index every track page's rows (present AND absent) so row extents are bounded
    # correctly — an absent row between the true owner and the match must still count.
    track_pages = {}     # pb -> sorted list of (row_base, R, present) for ALL rows
    present_before = 0
    for p in range(npages):
        pb = p * len_page
        if pb + 0x28 > len(data) or u4(data, pb + 8) != 0:     # type 0 = tracks
            continue
        rows = page_rows(data, len_page, pb)
        present_before += sum(1 for (_, pr, _) in rows if pr)
        if rows:
            track_pages[pb] = sorted((rb, R, pr) for (R, pr, rb) in rows)

    def owner(off):
        pb = (off // len_page) * len_page
        rows = track_pages.get(pb)
        if not rows:
            return None
        best = None
        for rb, R, pr in rows:                     # rows sorted by row_base
            if rb <= off:
                best = (pb, R, pr)
            else:
                break
        return best                                # (pb, R, present) of containing row

    import unicodedata
    to_delete = set()
    not_found = []
    for name in names:
        # macOS filenames are NFD; the pdb stores NFC. And DeviceSQL stores pure-ASCII
        # strings as UTF-8 but anything with a non-ASCII char as UTF-16LE — try all.
        forms = {name, unicodedata.normalize("NFC", name), unicodedata.normalize("NFD", name)}
        needles = []
        for v in forms:
            needles.append(v.encode("utf-8"))
            needles.append(v.encode("utf-16-le"))
        hit = None
        for needle in needles:
            idx = data.find(needle)
            while idx != -1:
                o = owner(idx)
                if o and o[2]:                     # containing row must be PRESENT
                    hit = (o[0], o[1])
                    break
                idx = data.find(needle, idx + 1)
            if hit:
                break
        if hit:
            to_delete.add(hit)
        else:
            not_found.append(name)

    for pb, R in to_delete:
        clear_present(data, len_page, pb, R)

    open(outp, "wb").write(data)
    present_after = sum(len([1 for (_, pr, _) in page_rows(data, len_page, pb) if pr])
                        for pb in [p * len_page for p in range(npages)]
                        if pb + 0x28 <= len(data) and u4(data, pb + 8) == 0)
    print(f"len_page={len_page} pages={npages}")
    print(f"present track rows: before={present_before}  after={present_after}  "
          f"removed={present_before - present_after}")
    print(f"names requested={len(names)}  matched={len(to_delete)}  not_found={len(not_found)}")
    for n in not_found[:15]:
        print("  NOT-FOUND:", n)


if __name__ == "__main__":
    main()
