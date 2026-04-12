import { useState, useEffect, useRef } from 'react';

export const useDataFetching = (url: string, intervalMs: number, defaultValue: any) => {
    const [data, setData] = useState(defaultValue);
    const backoffRef = useRef(0);

    useEffect(() => {
        let timer: ReturnType<typeof setTimeout>;
        let cancelled = false;
        const effectiveInterval = Math.max(intervalMs, 2000);

        const fetchData = async () => {
            try {
                const response = await fetch(url);
                if (response.ok) {
                    const text = await response.text();
                    try { setData(JSON.parse(text)); } catch { setData(text); }
                    backoffRef.current = 0;
                } else if (response.status === 429) {
                    const retryAfter = parseInt(response.headers.get('Retry-After') || '2', 10) * 1000;
                    backoffRef.current = retryAfter;
                }
            } catch {
                backoffRef.current = backoffRef.current === 0 ? 3000 : Math.min(backoffRef.current * 2, 60000);
            }
            if (!cancelled) {
                const delay = backoffRef.current > 0 ? backoffRef.current : effectiveInterval;
                timer = setTimeout(fetchData, delay);
            }
        };

        fetchData();
        return () => { cancelled = true; clearTimeout(timer); };
    }, [url, intervalMs]);

    return data;
};
