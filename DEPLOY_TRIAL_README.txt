TRIAL DEPLOYMENT — Project Estimation

This folder is prepared to deploy the Flask Project Estimation module on Render.

Render settings:
  Runtime: Python 3
  Build Command: pip install -r requirements.txt
  Start Command: gunicorn app:app --bind 0.0.0.0:$PORT

Important:
  - This is a TRIAL deployment of the Flask estimator module from v17.
  - The top-level Project Enquiry Tool in v17 is a separate custom HTTP server and
    expects Process_for_Application_.xlsx, which is not present in the uploaded zip.
  - The complete combined online architecture should be done later.
  - Render's default filesystem is ephemeral, so data written by this trial can be
    lost on restart/redeploy. Do not use this trial for real production records.
