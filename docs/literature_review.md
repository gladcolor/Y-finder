# Y-finder: Literature Review and Prior-Art Scan

This review informs Y-finder, a map-and-chat data guide that refines research questions by tri-projection and labels datasets by scope fit. References were verified November 8, 2026 via Crossref DOI records, publisher pages, or official product pages.

## 1. Dataset search and discovery

**Chapman, A., Simperl, E., Koesten, L., et al. (2020).** Dataset search: a survey. *The VLDB Journal*, 29(1), 251-272. https://doi.org/10.1007/s00778-019-00564-x  
Surveys dataset retrieval systems and separates basic search from "constructive" search, where users assemble data from several sources.  
*Implication for Y-finder:* Recommend bundles of joinable datasets, not single hits.

**Brickley, D., Burgess, M., & Noy, N. (2019).** Google Dataset Search: Building a search engine for datasets in an open Web ecosystem. *The World Wide Web Conference (WWW '19)*, 1365-1375. https://doi.org/10.1145/3308558.3313685  
Indexes datasets across the Web from schema.org and DCAT metadata that publishers embed in their pages. Coverage depends on what publishers mark up.  
*Implication for Y-finder:* Harvest standard metadata and flag thin records.

**Gregory, K., Groth, P., Scharnhorst, A., & Wyatt, S. (2020).** Lost or found? Discovering data needed for research. *Harvard Data Science Review*, 2(2). https://doi.org/10.1162/99608f92.e38165eb  
The largest known survey of how researchers find others' data; it asks designers to treat metadata as support for sensemaking and contact with people.  
*Implication for Y-finder:* Explain why a dataset fits and who can help, not only rank results.

**Viswanathan, V., Gao, L., Wu, T., Liu, P., & Neubig, G. (2023).** DataFinder: Scientific dataset recommendation from natural language descriptions. *Proceedings of ACL 2023 (Long Papers)*, 10288-10303. https://doi.org/10.18653/v1/2023.acl-long.573  
Frames dataset recommendation as retrieval from a short research-idea description. On 392 expert-labeled test queries, a trained bi-encoder beat existing dataset search engines.  
*Implication for Y-finder:* This is Y-finder's task, and a few hundred labeled questions suffice to compare retrievers.

## 2. Geospatial discovery infrastructure and usability

**Hardy, D., & Durante, K. (2014).** A metadata schema for geospatial resource discovery use cases. *The Code4Lib Journal*, 25. https://journal.code4lib.org/articles/9710  
Introduces the GeoBlacklight schema (Dublin Core plus GeoRSS), because ISO 19115/19139 and FGDC records are too complex for simple cross-library discovery.  
*Implication for Y-finder:* Yale's GeoBlacklight records offer harvestable bounding boxes and access rights.

**STAC Specification v1.1.0** (OGC Community Standard 25-004). https://stacspec.org ; https://www.ogc.org/standards/stac/ . **OGC API - Records - Part 1: Core** (OGC 20-004r1; approval announced July 1, 2025). https://docs.ogc.org/is/20-004r1/20-004r1.html  
STAC standardizes how imagery and other space-time assets are described and queried. OGC API Records is a modern Web API for searching catalogs, an alternative to the older Catalogue Service for the Web (CSW).  
*Implication for Y-finder:* Query STAC APIs for imagery and OGC API Records or CSW for other catalogs.

**Blake, M., Majewicz, K., Tickner, A., & Lam, J. (2017).** Usability analysis of the Big Ten Academic Alliance Geoportal: Findings and recommendations for improvement of the user experience. *The Code4Lib Journal*, 38. https://journal.code4lib.org/articles/12932  
In 16 user tests of a GeoBlacklight portal, most searches were place names, which failed when names were missing from metadata. Few users saw the map as a search tool, and many mistook the basemap preview for the data.  
*Implication for Y-finder:* Convert place names to extents and label clearly what on the map is data.

**Ankama, S., Degbelo, A., Naoseb, E., Henzen, C., & Bernard, L. (2026).** How well do current geoportals support geodata discovery? An empirical study. *ISPRS International Journal of Geo-Information*, 15(2), 82. https://doi.org/10.3390/ijgi15020082  
Think-aloud tests in Germany and Namibia found recurring problems in search, result presentation, user guidance, map interaction, and metadata descriptions.  
*Implication for Y-finder:* Use these five areas as the usability test checklist.

## 3. LLM agents for geospatial data retrieval and GIS

**Li, Z., & Ning, H. (2023).** Autonomous GIS: the next-generation AI-powered GIS. *International Journal of Digital Earth*, 16(2), 4668-4686. https://doi.org/10.1080/17538947.2023.2278895  
Proposes GIS with a large language model (LLM) as its reasoning core and five goals, including self-verifying and self-executing, demonstrated with the LLM-Geo prototype.  
*Implication for Y-finder:* Y-finder is autonomous GIS's data-discovery module; self-verifying means checking recommendations against catalog records.

**Ning, H., Li, Z., Akinboyewa, T., & Lessani, M. N. (2025).** An autonomous GIS agent framework for geospatial data retrieval. *International Journal of Digital Earth*, 18(1), 2458688. https://doi.org/10.1080/17538947.2025.2458688  
LLM-Find picks data sources using per-source handbooks, then fetches data by writing, running, and debugging code, succeeding on most of 70-plus example requests.  
*Implication for Y-finder:* Keep handbooks and code-based fetching, and add the missing step: judging fit before fetching.

**Akinboyewa, T., Li, Z., Ning, H., & Lessani, M. N. (2025).** GIS Copilot: towards an autonomous GIS agent for spatial analysis. *International Journal of Digital Earth*, 18(1). https://doi.org/10.1080/17538947.2025.2497489  
Embeds LLMs in QGIS for natural-language spatial analysis. Across 100-plus tasks, basic and guided tasks mostly succeeded; unguided advanced tasks remained hard.  
*Implication for Y-finder:* The GeoLibre hand-off should carry a scoped, step-by-step plan.

**Zhang, Y., Wei, C., He, Z., & Yu, W. (2024).** GeoGPT: An assistant for understanding and processing geospatial tasks. *International Journal of Applied Earth Observation and Geoinformation*, 131, 103976. https://doi.org/10.1016/j.jag.2024.103976  
Links an LLM to a pool of GIS tools for data download, spatial query, facility siting, and mapping (case studies).  
*Implication for Y-finder:* Tool-calling agents run operations but do not judge data fitness, which is Y-finder's niche.

**Chen, Y., Wang, W., Lobry, S., & Kurtz, C. (2024).** An LLM agent for automatic geospatial data analysis (GeoAgent). arXiv:2410.18792. https://arxiv.org/abs/2410.18792  
Combines a code interpreter, static analysis, and retrieval-augmented generation inside a tree search, beating baseline LLMs on a new geospatial coding benchmark.  
*Implication for Y-finder:* Retrieve each source's documentation before generating fetch code.

## 4a. Question refinement

**Taylor, R. S. (1968).** Question-negotiation and information seeking in libraries. *College & Research Libraries*, 29(3), 178-194. https://doi.org/10.5860/crl_29_03_178  
Shows that a stated question is often a "compromised" version of the real need, shaped by what users think the system can answer. Librarians recover the need through negotiation.  
*Implication for Y-finder:* A new user's first question is the compromised one; tri-projection is the negotiation.

**Partlo, K. (2009).** The pedagogical data reference interview. *IASSIST Quarterly*, 33(4), 6-10. https://iassistquarterly.com/public/pdfs/iqvol334_341partlo.pdf  
Argues that the data reference interview should teach as well as serve, because undergraduates are less ready than faculty to state data needs.  
*Implication for Y-finder:* Give each clarifying question a one-line reason, with more detail for students.

**Aliannejadi, M., Zamani, H., Crestani, F., & Croft, W. B. (2019).** Asking clarifying questions in open-domain information-seeking conversations. *Proceedings of SIGIR 2019*, 475-484. https://doi.org/10.1145/3331184.3331265  
Built Qulac, over 10K clarifying question-answer pairs. An oracle asking one good question raised precision at rank 1 by over 170%.  
*Implication for Y-finder:* One or two well-chosen questions can matter more than a better ranker.

## 4b. Fitness for use, scale, and FAIR data

**Devillers, R., Bédard, Y., Jeansoulin, R., & Moulin, B. (2007).** Towards spatial data quality information analysis tools for experts assessing the fitness for use of spatial data. *International Journal of Geographical Information Science*, 21(3), 261-282. https://doi.org/10.1080/13658810600911879  
Organizes data quality indicators in a hierarchy so experts can judge whether data suit a given use.  
*Implication for Y-finder:* Scope-fit labels are a simple fitness-for-use summary, with detail on demand.

**Goodchild, M. F. (2011).** Scale in GIS: An overview. *Geomorphology*, 130(1-2), 5-9. https://doi.org/10.1016/j.geomorph.2010.10.004  
Separates two meanings of scale, resolution and extent, and shows how scale choices affect cross-scale inference, including the modifiable areal unit problem (MAUP).  
*Implication for Y-finder:* Score extent and resolution separately, and warn about MAUP when data units differ from the unit of analysis.

**Wilkinson, M. D., Dumontier, M., Aalbersberg, I. J., et al. (2016).** The FAIR Guiding Principles for scientific data management and stewardship. *Scientific Data*, 3, 160018. https://doi.org/10.1038/sdata.2016.18  
Principles for findable, accessible, interoperable, and reusable data, stressing machine-actionable metadata.  
*Implication for Y-finder:* The access label turns FAIR "accessible" into a concrete Yale route.

## 5. Grounding and evaluation

**Lewis, P., Perez, E., Piktus, A., et al. (2020).** Retrieval-augmented generation for knowledge-intensive NLP tasks. *Advances in Neural Information Processing Systems 33 (NeurIPS 2020)*. https://papers.nips.cc/paper/2020/hash/6b493230205f780e1bc26945df7481e5-Abstract.html  
Pairs a generator with a retriever over a document index, giving more factual output than a generator alone.  
*Implication for Y-finder:* The LLM may only recommend datasets retrieved from the catalog index.

**Liu, N. F., Zhang, T., & Liang, P. (2023).** Evaluating verifiability in generative search engines. *Findings of EMNLP 2023*, 7001-7025. https://doi.org/10.18653/v1/2023.findings-emnlp.467  
In four generative search engines, only 51.5% of sentences were fully supported by citations, and 74.5% of citations supported their sentence.  
*Implication for Y-finder:* Verify that each cited record exists and that stated extent and dates match it.

**Voorhees, E. M. (1999).** The TREC-8 Question Answering Track report. *Proceedings of the Eighth Text REtrieval Conference (TREC-8)*, NIST, 77-82. https://www.nist.gov/publications/trec-8-question-answering-track-report  
Scored each question by the reciprocal rank of the first correct answer among five, averaged over questions: mean reciprocal rank (MRR).  
*Implication for Y-finder:* Report hit@k and MRR on real consult questions.

**Sun, Y., Wen, Y., & Yang, H. (2026).** ReSearch: A multi-stage machine learning framework for Earth science data discovery. arXiv:2601.14176. https://arxiv.org/abs/2601.14176  
Retrieves broadly with keywords and embeddings, then reranks with an LLM. Its benchmark links research goals to datasets cited in peer-reviewed papers.  
*Implication for Y-finder:* Build a second test set from data citations in Yale papers.

## 6. Map plus chat interfaces

**Cai, G., Wang, H., MacEachren, A. M., & Fuhrmann, S. (2005).** Natural conversational interfaces to geospatial databases. *Transactions in GIS*, 9(2), 199-221. https://doi.org/10.1111/j.1467-9671.2005.00213.x  
GeoDialogue sat between users and a GIS server, recognizing information needs and reasoning about map and database steps.  
*Implication for Y-finder:* Conversational GIS began as dialogue about needs, not commands.

**Tao, R., & Xu, J. (2023).** Mapping with ChatGPT. *ISPRS International Journal of Geo-Information*, 12(7), 284. https://doi.org/10.3390/ijgi12070284  
ChatGPT lowered barriers to map making, but benefits were unequal across users and quality depended on user checks.  
*Implication for Y-finder:* Novices need visible checks, not only fluent answers.

**GeoLibre. AI Assistant (user guide).** https://geolibre.app/user-guide/ai-assistant/  
A chat panel maps plain-English requests to GeoLibre's own validated operations, such as styling, spatial SQL, buffer, clip, and Planetary Computer STAC search. Tool calls are shown, most actions can be undone, and the LLM provider is pluggable.  
*Implication for Y-finder:* GeoLibre is the hand-off target; its validated-operations design suits Y-finder's own map actions.

**Esri. ArcGIS AI assistants documentation.** https://doc.arcgis.com/en/arcgis-online/administer/configure-assistants.htm ; https://doc.arcgis.com/en/teams/latest/get-started/arcgis-for-teams-assistant.htm ; https://doc.arcgis.com/en/arcgis-online/reference/doc-assistant.htm  
ArcGIS Online lists about a dozen assistants, mostly beta or preview. The Teams assistant recommends data from prompts, and the Documentation assistant answers only from Esri documentation, with sources. Pages warn that suggestions "can be misleading or inaccurate."  
*Implication for Y-finder:* Pair every answer with sources, and reach Esri content through Yale's organizational account.

## Prior-art scan

| Tool | Does well | Lacks for a new-to-GIS researcher | Y-finder borrows |
|---|---|---|---|
| Google Dataset Search | Web-wide index of schema.org dataset pages; simple keyword search. | No question refinement or fit labels; no AI search described. | Harvest schema.org and DCAT metadata. |
| Earth Engine Data Catalog | Curated imagery and climate collections; tag pages; STAC JSON catalog; community catalogs. | Assumes Earth Engine coding; no natural-language search found. | Ingest its STAC JSON; code snippets as hand-off. |
| Microsoft Planetary Computer | Open STAC API and catalog; data and APIs continued after the Hub closed June 6, 2024. | Imagery focus; users must know collection names and bands. | STAC adapter (the catalog GeoLibre already searches). |
| NASA Earthdata Search | Deep NASA search with space and time filters. AI: Earth Copilot, a NASA-only proof of concept (2024); NASA IMPACT's CMR Agent clarifies bounds, explains results, and links out (status not stated). | Jargon (collections, granules); no built-in natural-language search found. | CMR Agent pattern: clarify, explain, list what to verify. |
| Esri Living Atlas and AI assistants | Curated, map-ready layers; Teams assistant (beta) suggests data from prompts. | Premium content needs an organizational account and credits; assistants are beta; no fit labels. | Access-route labels; sources in answers. |
| GeoLibre AI assistant | Validated operations, visible tool calls, undo, STAC imagery search. | Works on chosen data; no broad catalog or fit reasoning. | Hand-off target; validated-operation design. |
| Data.gov | 535,132 datasets; map box filter (Within or Intersect); geospatial-only and downloadable-only filters. | About 256,000 Census Bureau records crowd results; no natural-language search. | Within/Intersect logic for extent fit. |
| Yale Library GeoBlacklight | Yale geodata with data type, place, subject, and language facets; find by location. | Keyword and facet search only; no access facet or guidance on the landing page. | Authoritative Yale records and access rights. |
| Yale Dataverse | Library-run repository (368 datasets) with facets and DOIs; "Spatial Datasets for GIS Classes." | Few geospatial records (23 tagged); no map search. | DOI citations; teaching datasets. |

Table sources: https://datasetsearch.research.google.com/help ; https://developers.google.com/earth-engine/datasets/community ; https://github.com/google/earthengine-catalog ; https://github.com/microsoft/PlanetaryComputer/discussions/347 ; https://blogs.microsoft.com/blog/2024/11/14/from-questions-to-discoveries-nasas-new-earth-copilot-brings-microsoft-ai-capabilities-to-democratize-access-to-complex-data/ ; https://nasa-impact.github.io/AI-Agents-for-Science/agents/cmr ; https://enterprise.arcgis.com/en/portal/11.4/use/what-is-living-atlas.htm ; https://catalog.data.gov/dataset ; https://geoblacklight.library.yale.edu/ ; https://dataverse.yale.edu/ . Counts as shown November 8, 2026.

## Design implications

- **Clarifying questions first.** Ask one to three questions that fill the scope (extent, time window, resolution, unit of analysis), as clickable options with a one-line reason (Aliannejadi et al.; Partlo).
- **Show the negotiation.** Display the influence graph so users see how factors became data variables (Taylor). For uploaded proposals, extract the draft scope for the user to confirm.
- **Scope-fit labels from metadata.** Grade extent (bounding-box overlap), time (date overlap), resolution (pixel size or areal unit versus target), and access as fits, partial, or mismatch, with a reason. Compute labels from record fields, not LLM opinion, and flag MAUP when units differ (Goodchild; Devillers et al.).
- **Catalog-grounded citations.** Every recommendation carries a record ID and link. A validator rejects IDs not in the index and checks stated extent and dates against the record (Lewis et al.; Liu et al.).
- **Hybrid retrieval.** Combine keywords, embeddings, place-name geocoding, and LLM reranking (DataFinder; ReSearch) over live STAC, OGC API Records, GeoBlacklight, Dataverse, and Data.gov endpoints.
- **Yale access routes.** Label each dataset as open, Yale login, Yale ArcGIS account (Business Analyst, Living Atlas premium), or licensed through YCGS or the Library (Planet, Dewey Data), with the exact next step.
- **Evaluation set from real consult questions.** Collect de-identified YCGS consult questions with the datasets staff chose. Report hit@k, MRR, citation precision and recall, and label accuracy against a keyword baseline, plus a set from data citations in Yale papers (Voorhees; Liu et al.; ReSearch).
- **Honest map preview.** Draw each dataset's footprint against the study area, separate data from basemap, and show plain-language metadata (Blake et al.; Ankama et al.).
- **Hand-off to GeoLibre.** Pass selected layers, the confirmed scope, and a step-by-step plan, since guided tasks succeed more often (Akinboyewa et al.).
- **Human in the loop.** Like the CMR Agent, list what to verify, and offer an "Ask YCGS" button that sends the chat summary to staff (Gregory et al.).

## Verification notes

Dropped as unverifiable: He, Persson, and Östman (2012), "Geoportal usability evaluation" (findings not readable); Google Dataset Search license and download percentages (secondary source only); built-in natural-language search in NASA Earthdata Search; an Esri Map Viewer assistant that searches Living Atlas; studies of LLMs inventing dataset names. GeoAgent is cited as its arXiv version.
