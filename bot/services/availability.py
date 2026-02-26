from datetime import date, timedelta

# Python weekday(): 0=Mon, 1=Tue, 2=Wed, 3=Thu, 4=Fri, 5=Sat(closed), 6=Sun
WORK_DAYS = {0, 1, 2, 3, 4, 6}
SLOTS = ["09:00", "10:00", "11:00", "12:00", "13:00", "14:00"]


def get_available_days(days_ahead: int = 7) -> list[date]:
    today = date.today()
    return [
        today + timedelta(days=i)
        for i in range(1, days_ahead + 1)
        if (today + timedelta(days=i)).weekday() in WORK_DAYS
    ]


def get_available_hours(day: date) -> list[str]:
    from bot.services.appointments import get_booked_slots
    booked = get_booked_slots(day)
    return [s for s in SLOTS if s not in booked]
