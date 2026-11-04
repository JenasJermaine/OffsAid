# Scam-language model

This folder holds the fine-tuned classifier (`Davlan/afro-xlmr-small`, 7 labels).
The weights are not in git. GitHub rejects files over 100 MB, and the model is
about 500 MB.

The copy to unpack is `offsaid_classifier_v4.zip`, saved by
`ml/scam_language/notebooks/OffsAid_classifier_training.ipynb` (`SAVE_DIR`).
The notebook name was not bumped; the training data is the v5 synthetic set.
The zip was copied to the author's Google Drive (`MyDrive/offsaid_classifier_v4.zip`).
There is no public download yet. Later this should be a private Hugging Face repo
fetched at startup.

Unpack it so the files sit directly in `models/offsaid_classifier_v4/`:

```text
config.json
model.safetensors
tokenizer.json
tokenizer_config.json
thresholds.json
```

`config.json` should show `num_labels` 7 and these `id2label` entries:
`advance_fee_request`, `personal_account_payment`, `urgency_pressure`, `secrecy`,
`guaranteed_outcome`, `visa_travel_arranged`, `off_platform_move`.

`OFFSAID_MODEL_DIR` defaults to `models/offsaid_classifier_v4`. On a server the
folder has to be downloaded or mounted before startup, because it will not arrive
through git. The loaded model needs about 1 to 1.5 GB of RAM. Run one worker
process (the systemd unit already does); each extra worker would load its own copy.
If the folder is missing or the load fails, the API stays up and the check runs
in `rules_only` mode (the fee rule only). The response field `mode` says so.
