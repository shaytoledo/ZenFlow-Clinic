# Point Image Sourcing (Phase 4.3d → owner decision Q4)

The plan says to bring back a shortlist **with licences** before downloading anything. Nothing
has been downloaded. The pipeline is ready for whichever source the owner picks:

- `python -m zenflow.ingest_images` refuses any file without a credit and a licence;
- `ZF_POINT_IMAGES` keeps images hidden until then.

Licences were checked on 2026-09-17 at the source pages listed below. Re-check each file at
download time: licences on shared collections are set **per file**.

## What the treatment page needs

- **Coverage:** one clear **diagram per point** for the 26 points in `acupoints` today, with the
  location marked on the body region. The 361 standard points come later.
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

## How a chosen set gets in

1. Put the files in a folder named by code (`LI4.png`, `ST-36_diagram.svg`→`.png`). The
   ingester accepts PNG, JPEG and WebP.
2. Write `credits.json` with the credit, licence, licence URL and source URL, as a folder-wide
   default or per file.
3. Run `python -m zenflow.ingest_images <folder> --dry-run`, read the report, then run it
   without `--dry-run`.
4. Set `ZF_POINT_IMAGES=1`.
