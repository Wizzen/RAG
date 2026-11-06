from __future__ import annotations

"""五字段 cron：列表、范围、步长、月/星期名称及标准日字段语义。"""

from datetime import datetime,timedelta,timezone
from zoneinfo import ZoneInfo,ZoneInfoNotFoundError

MONTHS={name:i for i,name in enumerate('jan feb mar apr may jun jul aug sep oct nov dec'.split(),1)}
DAYS={name:i for i,name in enumerate('sun mon tue wed thu fri sat'.split())}


class CronSchedule:
    def __init__(self,expression: str,zone: str='Asia/Shanghai'):
        if not isinstance(expression,str) or len(expression)>100 or len(expression.split())!=5:
            raise ValueError('Cron 必须是五字段：分 时 日 月 星期。')
        try:self.zone=ZoneInfo(zone)
        except (ZoneInfoNotFoundError,ValueError,TypeError) as exc:raise ValueError('请使用有效 IANA 时区，例如 Asia/Shanghai。') from exc
        self.fields=expression.lower().split()
        self.minutes,self.hours,self.dom,self.months,self.dow=[self.parse(part,low,high,names) for part,low,high,names in
            zip(self.fields,[0,0,1,1,0],[59,23,31,12,7],[{}, {}, {}, MONTHS, DAYS])]
        self.dow={value%7 for value in self.dow}

    @staticmethod
    def parse(field,low,high,names):
        values=set()
        def number(text):
            if text in names:return names[text]
            if not text.isascii() or not text.isdecimal():raise ValueError('Cron 字段包含不支持的字符。')
            value=int(text)
            if not low<=value<=high:raise ValueError('Cron 数值超出允许范围。')
            return value
        for term in field.split(','):
            parts=term.split('/')
            if len(parts)>2:raise ValueError('Cron 步长格式无效。')
            base=parts[0];step=1
            if len(parts)==2:
                if not parts[1].isascii() or not parts[1].isdecimal() or not 1<=int(parts[1])<=high-low+1:raise ValueError('Cron 步长无效。')
                step=int(parts[1])
            if base=='*':left,right=low,high
            elif '-' in base:
                limits=base.split('-')
                if len(limits)!=2:raise ValueError('Cron 范围无效。')
                left,right=map(number,limits)
                if left>right:raise ValueError('Cron 范围起点不能大于终点。')
            else:
                if len(parts)==2:raise ValueError('Cron 步长请搭配 * 或范围。')
                left=right=number(base)
            values.update(range(left,right+1,step))
        return values

    def day_matches(self,day):
        dom=day.day in self.dom;dow=(day.weekday()+1)%7 in self.dow
        return day.month in self.months and ((dom and dow) if '*' in self.fields[2] or '*' in self.fields[4] else (dom or dow))

    def matches(self,at: datetime) -> bool:
        local=at.astimezone(self.zone)
        return local.minute in self.minutes and local.hour in self.hours and self.day_matches(local.date())

    def next_after(self,at: datetime) -> datetime:
        if at.tzinfo is None:raise ValueError('调度时间必须包含时区。')
        first=at.astimezone(self.zone).date()
        for offset in range(366*5):
            day=first+timedelta(days=offset)
            if not self.day_matches(day):continue
            candidates=set()
            for hour in sorted(self.hours):
                for minute in sorted(self.minutes):
                    for fold in (0,1):
                        local=datetime(day.year,day.month,day.day,hour,minute,tzinfo=self.zone,fold=fold)
                        utc=local.astimezone(timezone.utc)
                        if utc.astimezone(self.zone).replace(tzinfo=None)!=local.replace(tzinfo=None):continue
                        if utc>at:candidates.add(utc)
            if candidates:return min(candidates)
        raise ValueError('Cron 在未来五年没有可运行时间。')
