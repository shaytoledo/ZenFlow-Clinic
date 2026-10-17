# Point Image Sourcing (Phase 4.3d → owner decision Q4)

The plan says to bring back a shortlist **with licences** before downloading anything. Nothing
has been downloaded. The pipeline is ready for whichever source the owner picks:

- `python -m zenflow.ingest_images` refuses any file without a credit and a licence;
- `ZF_POINT_IMAGES` keeps images hidden until then.

Licences were checked on 2026-09-17 at the source pages listed below. Re-check each file at
download time: licences on shared collections are set **per file**.

## What the treatment page needs

- **Coverage:** one clear **diagram per point**, with the location marked on the body region.
  When this was written `acupoints` held 26 points; since Phase 14.1 it holds the 361 standard
  points and 33 extra points.
- **Accuracy:** it must match the WHO standard location that the card text describes.
- **Clean files:** no watermark, and nothing that clashes with the card design. SVG or high-res
  PNG.
- **Licensing:** a licence that allows use inside a commercial clinic's software, with an
  attribution line we can show (the lightbox shows credit, licence and source).

## Shortlist

| # | Source | What it has | Licence (as published) | Fit |
|---|---|---|---|---|
| 1 | **Commission our own SVG set**, drawn to the WHO 2008 locations | Exactly the 26 points, one style, labels in EN/HE, scalable | Owned by the clinic (or published by the clinic as CC BY) | **Best.** No share-alike obligations and consistent with the design. Costs illustrator time |
| 2 | [HOPE Neuro-Acupuncture Rehab — meridian points chart](https://www.neuroacupuncturerehab.com/blog/meridian-points-chart/) | **Individual point diagrams** (e.g. LU-1, ST-36, BL-67) | **CC BY-SA 4.0**, attribution "© 2024 by HOPE Neuro-Acupuncture Rehab" | Good coverage and allowed commercially. **Share-alike:** edited versions must also be CC BY-SA. Their style is not ours. Check each point is included |
| 3 | [Wikimedia Commons — Category:Acupuncture points](https://commons.wikimedia.org/wiki/Category:Acupuncture_points) (86 files) and [Category:Acupuncture charts](https://commons.wikimedia.org/wiki/Category:Acupuncture_charts) | Mostly historical Chinese woodcuts and lithographs (Wellcome Collection), a few modern photos (e.g. `Acupuncture_point_Hegu_(LI_4).jpg`), some GIF point diagrams (e.g. `Lu_7.gif`) | **Per file**: many Wellcome images are CC BY 4.0, others public domain or CC BY-SA | Fine for historical context. **Not** a consistent clinical set: styles and accuracy vary, and every file needs its own licence check |
| 4 | [BodyParts3D](https://github.com/Kevin-Mattheus-Moerman/BodyParts3D) / Z-Anatomy (DBCLS, University of Tokyo) | 3D anatomy meshes, no acupoints | **CC BY-SA 2.1 Japan**, credit "BodyParts3D, (c) The Database Center for Life Science" | A base for drawing our own 3D-style point images (`kind = 3d`). Renders would be share-alike |
| 5 | [WHO Standard Acupuncture Point Locations in the Western Pacific Region (2008)](https://iris.who.int/handle/10665/353407) | The reference locations for 361 points, with figures | WHO publication; the IRIS page refused automated access, and the book is sold. **Treat as all rights reserved** unless WHO grants permission | **Reference only:** our text and any commissioned drawings follow its locations. Do not copy its figures |

**Excluded:** the StatPearls acupuncture figure on
[NCBI Bookshelf](https://www.ncbi.nlm.nih.gov/books/NBK532287/figure/article-17141.image.f1/).
The book is **CC BY-NC-ND 4.0**, which rules out non-commercial-only use in a clinic product and
any modification. The chart inside it is itself a Wellcome CC BY 4.0 image, which option 3
already covers.

## Recommendation

1. **Now:** option 1. Commission 26 SVG diagrams, then extend them as the point table grows.
   Owned artwork removes the share-alike question and keeps the cards consistent.
2. **Meanwhile, if images are wanted sooner:** option 2, accepting CC BY-SA 4.0 for those files.
   Attribution is already shown by the lightbox.
3. **Optional:** a "historical chart" tab using Wellcome images from option 3, clearly labelled
   as historical.

## Owner decision Q4 (2026-10-02): "research it yourself" — the choice

Re-checked against our 26 points:
- **Wikimedia Commons** (86 files in the category): mostly historical woodcuts and a handful of
  modern GIFs. There is no consistent modern set, so it stays a source for a historical tab only.
- **Acupoint Wellness Atlas** and similar sites state no licence, so they are all rights reserved.
- **HOPE Neuro-Acupuncture Rehab** publishes one diagram per point for all 361 meridian points
  (362 images on the page). The licence is stated under each image: **CC BY-SA 4.0**, credit
  "© 2024 by HOPE Neuro-Acupuncture Rehab", with the note "Blog Visuals are Free to Use with
  Attribution". The images are WebP, about 39 KB each.
  **Coverage: 25 of our 26 points** — every meridian point. **Yintang** (an extra point) is missing.

**Choice:** option 2 now, so that 25 points get a real diagram. Yintang keeps the placeholder until
the commissioned set (option 1) or another licensed diagram covers it. Share-alike applies to the
image files only: we show them unmodified, with credit and licence in the lightbox. That does not
make the app itself CC BY-SA. **Next step:** the owner approves downloading the 25 files
(~1 MB), then the steps below.

## Done on 2026-10-12 (Phase 14.3)

The owner answered Q4 with "whatever you think", and asked for many more points. The table now
holds the 361 WHO points (14.1), and the source has a diagram for each.

- **The manifest:** `zenflow/seed_data/point_images.json` names one diagram for each of the 361
  WHO points, with the source's credit and licence. It was built from the source page on
  2026-10-12: 361 figures in 14 sections whose sizes match the channels exactly, and **every figure
  carries its own "CC BY-SA 4.0" caption**.
- **Three captions on the source page name another point** (ST28 says ST-45, BL21 says BL-28,
  BL65 says BL-64). Position and file name agree with each other, so the manifest follows them and
  carries a `note`. Look at those three after fetching them.
- **Fetched and imported: the 25 diagrams the owner approved** (the 26 original points without
  Yintang): 1.1 MB. Each file's SHA-256 is pinned in the manifest. They are in the host's
  database with credit, licence and source address, and `ZF_POINT_IMAGES=1` is set.
- **Not fetched: the other 336** (about 15 MB more). That is a larger download than the one the
  owner approved, so it waits for a yes (Q18).
- **What the diagrams are:** a body silhouette with the point marked and its code, Han characters
  and name. They show the region, not surface anatomy. The card's text gives the exact location.
- **Shown as published:** the ingester resizes to 1600 px and strips metadata, and changes nothing
  else. The lightbox shows the credit, the licence and a link to the source.

## 2026-10-17: all 361 fetched; the Bladder and Gallbladder drawings rejected (Phase 14.5)

The owner approved the other 336 (Q18). All 361 were fetched (15 MB) and pinned. **Then 27 of them
were opened one by one**, covering every channel, and two whole sets turned out to be wrong:

| Set | Opened | What is wrong |
|---|---|---|
| **Bladder (67)** | BL1, BL10, BL20, BL21, BL23, BL40, BL60, BL64, BL65, BL66 | **Every one** carries another point's Han characters: 臑俞 (SI10's), or 少澤 (SI1's) on BL1. Back points (BL10, BL20, BL21, BL23) are drawn on the **front** of the body, at the organs |
| **Gallbladder (44)** | GB20, GB21, GB30, GB34 | **Every one** carries 關衝 (TE1's characters). **GB21 is drawn on the chest**, not on the top of the shoulder |
| The other 12 channels | LU7, LI4, ST28, ST36, SP6, HT7, SI10, KI7, PC6, TE5, LR3, CV12, GV20 | Correct code, characters, name and region (SP6's pinyin is misspelled "Shanyijiao" in the drawing) |

- **Decision:** the 111 Bladder and Gallbladder diagrams carry `reject` in the manifest, with the
  evidence. The fetch command refuses them and `--all` skips them. On the host their files were
  moved to `data/point_images_rejected/`, and the 6 already imported (BL23, BL40, BL60, BL67,
  GB20, GB21) were deleted from the database and the media store.
- **Imported: 250 diagrams**, from the 12 channels with no defect found. 223 of them were not
  opened one by one: the check was a sample of every channel, not a review of every file.
- **The Bladder and Gallbladder points, and the 33 extra points, show the placeholder.** A
  commissioned set (option 1) or another licensed source would fill them.

```bash
python -m zenflow.fetch_point_images --list                         # what the manifest offers
python -m zenflow.fetch_point_images data/point_images_src LI4 ST36 # these points
python -m zenflow.fetch_point_images data/point_images_src --all    # every diagram not rejected
python -m zenflow.ingest_images data/point_images_src --dry-run     # then without --dry-run
```

The fetch command refuses any host but the manifest's, anything but HTTPS, a redirect, a file over
2 MB, bytes that are not WebP, and a file whose SHA-256 differs from a pinned one
(`tests/unit/test_fetch_point_images.py`). The extra points have no diagram at this source; they
keep the placeholder.

## How a chosen set gets in

1. Put the files in a folder named by code (`LI4.png`, `ST-36_diagram.svg`→`.png`). The
   ingester accepts PNG, JPEG and WebP.
2. Write `credits.json` with the credit, licence, licence URL and source URL, as a folder-wide
   default or per file.
3. Run `python -m zenflow.ingest_images <folder> --dry-run`, read the report, then run it
   without `--dry-run`.
4. Set `ZF_POINT_IMAGES=1`.
