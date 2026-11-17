"""Sample archive coverage by date/field and exact station; no continuous-coverage claim."""
import argparse
import json
from datetime import datetime,timedelta
from pathlib import Path
from urllib.parse import urlencode
from race_weather import ARCHIVE,SOURCES,RaceWeatherProvider,HKT,parse_observations
from persistence import atomic_json


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dates',nargs='+',required=True,help='ISO meeting dates to sample')
    parser.add_argument('--report',type=Path,default=Path('output/weather_coverage.json'))
    args=parser.parse_args()
    provider=RaceWeatherProvider()
    results=[]
    try:
        for day in args.dates:
            for dataset,spec in SOURCES.items():
                date=datetime.fromisoformat(day).strftime('%Y%m%d')
                url=ARCHIVE+'list-file-versions?'+urlencode(dict(url=spec['url'],start=date,end=date))
                record={'date':day,'dataset':dataset,'source_url':url,'stations':{},'status':'UNVERIFIED'}
                try:
                    code,body=provider._fetch(url)
                    if code==404:
                        record['status']='ARCHIVE_UNAVAILABLE'
                    else:
                        data=json.loads(body);stamps=data['timestamps']
                        if not isinstance(stamps,list) or len(stamps)>=10000:
                            raise ValueError('Invalid/truncated versions')
                        record.update(version_count=len(stamps),first_capture=min(stamps) if stamps else None,last_capture=max(stamps) if stamps else None)
                        if not stamps:
                            record['status']='NO_ARCHIVE_FOR_DATE'
                        else:
                            last=max(stamps)
                            download=ARCHIVE+'get-file?'+urlencode(dict(url=spec['url'],time=last))
                            code,payload=provider._fetch(download,max_age=365*86400)
                            if code==404:
                                record['status']='SNAPSHOT_UNAVAILABLE'
                            else:
                                at=datetime.strptime(last,'%Y%m%d-%H%M').replace(tzinfo=HKT).isoformat()
                                for station in ['Sha Tin','Happy Valley']:
                                    values=parse_observations(payload,dataset,station,at,download)
                                    record['stations'][station]={f:values.get(f,{}).get('status','STATION_NOT_REPORTED') for f in spec['fields']}
                                record['status']='SAMPLED_LAST_SNAPSHOT'
                except Exception as exc:
                    record.update(status='FAILED',error=type(exc).__name__)
                results.append(record)
    finally:
        provider.close()
    atomic_json(args.report,{'scope':'Sampled days and last snapshot only. Not a continuous historical year-by-year guarantee.',
                            'attribution':'Hong Kong Observatory, HKSAR Government and DATA.GOV.HK; provisional observations.', 'results':results})
    print(f'Audited {len(results)} date/dataset combinations; see {args.report}')


if __name__=='__main__':
    main()
