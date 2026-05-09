# Comic Translator Context

This context defines the domain language for translating Japanese comic images into Korean translated images. It separates text-derived grouping from image-derived speech bubble geometry so pipeline decisions stay precise.

## Language

**CV Text Fragment**:
A minimum OCR text unit returned by Cloud Vision with text and a bbox.
_Avoid_: character, block

**Text Line**:
A reading-direction sequence of CV Text Fragments grouped as one visual line.
_Avoid_: row

**Text Group**:
A text-derived utterance candidate produced by grouping Text Lines with bbox topology.
_Avoid_: cluster when discussing domain concepts, speech bubble

**Bubble Contour Candidate**:
An OpenCV-detected closed contour that may represent a Speech Bubble Region.
_Avoid_: real bubble

**Speech Bubble Region**:
A Bubble Contour Candidate that is matched to one or more Text Groups.
_Avoid_: R bubble, contour

**Split Bubble Region**:
A child region produced when a Speech Bubble Region is split by inflection analysis.
_Avoid_: F bubble

**Translation Segment**:
A final OCR and translation unit reconstructed by Gemini from the image and Text Group hints.
_Avoid_: sentence

## Relationships

- A **CV Text Fragment** belongs to zero or one **Text Line**.
- A **Text Line** belongs to zero or one **Text Group**.
- A **Text Group** may match zero or one **Speech Bubble Region**.
- A **Speech Bubble Region** may contain one or more **Text Groups**.
- A **Speech Bubble Region** may produce one or more **Split Bubble Regions**.
- A **Translation Segment** may reference one or more **Text Groups**.

## Example Dialogue

> **Dev:** "This **Text Group** spans two visible bubbles. Should we call it one **Speech Bubble Region**?"
> **Domain expert:** "No. The **Text Group** came from bbox topology; the **Speech Bubble Region** must come from a matched contour."

## Flagged Ambiguities

- "cluster" appears in JSON and code for historical compatibility, but the domain term is **Text Group**.
- Legacy "R Bubble" is resolved as **Speech Bubble Region** when the region is verified by matched Text Groups, not by local OCR.
- Legacy "F Bubble" is resolved as **Split Bubble Region** and should only be introduced after a Speech Bubble Region is split.
