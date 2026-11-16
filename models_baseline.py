"""Retrospective baseline evaluation; not a verified pre-race forecasting service."""
import pickle
from pathlib import Path
from datetime import datetime, timezone
import pandas as pd
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.metrics import roc_auc_score, average_precision_score, log_loss, brier_score_loss
from persistence import atomic_json

MODEL_PATH = Path(__file__).parent / 'model.pkl'
# Explicit allowlist prevents final odds, full-day weather and outcome-derived numeric fields entering the model.
SAFE_FEATURES = ['starts_before', 'win_sum_50', 'win_rate_50']


def train_baseline(df: pd.DataFrame, target_col: str = 'is_win'):
    required={target_col,'race_date','race_id',*SAFE_FEATURES}
    if required.difference(df.columns):
        raise ValueError('Missing historical feature/date schema: '+str(sorted(required.difference(df.columns))))
    df=df.dropna(subset=[target_col]).sort_values(['race_date','race_id']).copy()
    df['race_date']=pd.to_datetime(df['race_date'],errors='raise').dt.date
    dates=sorted(df.race_date.unique())
    if len(dates)<5:
        raise ValueError('At least five distinct meeting dates required for chronological train/validation/test evaluation')
    train_end=max(1,int(len(dates)*.6)); validation_end=max(train_end+1,int(len(dates)*.8))
    windows=[df[df.race_date.isin(dates[:train_end])], df[df.race_date.isin(dates[train_end:validation_end])], df[df.race_date.isin(dates[validation_end:])]]
    train,validation,test=windows
    fill_values=train[SAFE_FEATURES].replace([float('inf'),float('-inf')],float('nan')).median().fillna(0.)
    if train[target_col].nunique()<2:
        raise ValueError('Training window must contain winners and non-winners')
    model=GradientBoostingClassifier(n_estimators=50,random_state=0)
    def matrix(window):
        return window[SAFE_FEATURES].replace([float('inf'),float('-inf')],float('nan')).fillna(fill_values)
    model.fit(matrix(train),train[target_col].astype(int))
    metrics={}
    for label,window in [('validation',validation),('test',test)]:
        y=window[target_col].astype(int)
        p=model.predict_proba(matrix(window))[:,list(model.classes_).index(1)]
        picked=window.assign(probability=p).sort_values('probability',ascending=False).drop_duplicates('race_id')
        bins=pd.DataFrame({'p':p,'y':y.to_numpy()}).groupby(pd.cut(p,[0,.2,.4,.6,.8,1],include_lowest=True),observed=True).agg({'p':'mean','y':['mean','count']})
        metrics[label]={'roc_auc':float(roc_auc_score(y,p)) if y.nunique()>1 else None,
            'pr_auc':float(average_precision_score(y,p)) if y.nunique()>1 else None,
            'log_loss':float(log_loss(y,p,labels=[0,1])), 'brier':float(brier_score_loss(y,p)),
            'top_pick_hit_rate':float(picked[target_col].mean()), 'rows':len(window),
            'dates':[str(window.race_date.min()),str(window.race_date.max())],
            'calibration':[{ 'bin':str(i),'mean_probability':float(r[('p','mean')]),
                'win_rate':float(r[('y','mean')]),'count':int(r[('y','count')])} for i,r in bins.iterrows()]}
    artifact={'model':model,'feature_columns':SAFE_FEATURES,'fill_values':fill_values.to_dict(),
              'trained_at':datetime.now(timezone.utc).isoformat(),'evaluation':metrics,
              'purpose':'RETROSPECTIVE_ONLY','pre_race_verified':False,
              'limitations':'Completed-race cohort; historical result publication cutoffs not verified; no profitability claim'}
    MODEL_PATH.write_bytes(pickle.dumps(artifact))
    atomic_json(MODEL_PATH.with_suffix('.evaluation.json'),{k:v for k,v in artifact.items() if k!='model'})
    return MODEL_PATH


def train_from_scraped_data():
    from feature_engineering import example_pipeline
    features=example_pipeline()
    if features is None:
        raise ValueError('No usable race JSON data found')
    return train_baseline(pd.DataFrame(features.to_dicts()))


if __name__=='__main__':
    try:
        print(train_from_scraped_data())
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
