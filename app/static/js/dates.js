/**
 * DogBoxxDates — calendar-date helpers shared by layout.html (client) and
 * admin_layout.html, loaded in <head> so page scripts can rely on them.
 *
 * Dates are 'YYYY-MM-DD' strings in the business's calendar day
 * (Europe/London), matching app/utils/dates.py::local_today().
 *
 * Never build a date string by round-tripping a local-midnight Date through
 * toISOString(): that prints UTC, so during BST it lands a day early. The
 * arithmetic here works on UTC midnight instead, where toISOString() reads
 * back the same calendar date in any browser time zone.
 */
(function () {
    var londonParts = new Intl.DateTimeFormat('en-GB', {
        timeZone: 'Europe/London', year: 'numeric', month: '2-digit', day: '2-digit',
    });

    function parse(iso) { return new Date(iso + 'T00:00:00Z'); }
    function format(d) { return d.toISOString().slice(0, 10); }

    window.DogBoxxDates = {
        // Today's London date. Assembled from parts rather than relying on a
        // locale (e.g. en-CA) happening to format as YYYY-MM-DD.
        today: function () {
            var p = {};
            londonParts.formatToParts(new Date()).forEach(function (x) { p[x.type] = x.value; });
            return p.year + '-' + p.month + '-' + p.day;
        },
        addDays: function (iso, n) {
            var d = parse(iso);
            d.setUTCDate(d.getUTCDate() + n);
            return format(d);
        },
        addYears: function (iso, n) {
            var d = parse(iso);
            d.setUTCFullYear(d.getUTCFullYear() + n);
            return format(d);
        },
    };
})();
