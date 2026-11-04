# Scam-language training assets

Not used at runtime. The API loads the unpacked model from `models/` (see
`models/README.md`) and the inference code in `app/scam_language.py`.

| Path | What it is |
| --- | --- |
| `generate_synthetic.py` | Synthetic data generator (v5). Needs `pandas`. Writes `synthetic_train.csv` and `synthetic_val.csv` in the current directory. |
| `data/` | The generated train (5,000) and validation (1,000) rows. Still synthetic. |
| `notebooks/OffsAid_classifier_training.ipynb` | Colab notebook that produced the saved model. |
| `gold/` | Reserved for a real labelled set. Empty on purpose. Do not train on it. |

Real-world accuracy has not been measured. There is no gold set in this prototype.
Do not quote an accuracy figure for the classifier.
