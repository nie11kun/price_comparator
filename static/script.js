// static/script.js
document.addEventListener('DOMContentLoaded', () => {
    // --- DOM Element References ---
    const appSelect = document.getElementById('app-select');
    const planSelect = document.getElementById('plan-select');
    const priceTableBody = document.getElementById('price-table-body');
    const loadingDiv = document.getElementById('loading');
    const errorDiv = document.getElementById('error');
    const lastUpdatedDiv = document.getElementById('last-updated');

    let currentPlanList = [];

    // --- Function to Populate Plan Dropdown ---
    function populatePlanOptions(prices) {
        const planSet = new Set();
        if (prices && Array.isArray(prices)) {
            prices.forEach(item => {
                if (item.plan_name) { planSet.add(item.plan_name); }
                else if (appSelect.value === 'iCloud+' && item.region === 'US') {
                     const priceMap = {0.99: '50GB', 2.99: '200GB', 9.99: '2TB', 29.99: '6TB', 59.99: '12TB'};
                     if (item.price in priceMap) { planSet.add(priceMap[item.price]); }
                 }
                 // Add similar inference for Google One if needed
                 // else if (appSelect.value === 'Google One' && ...) { ... }
            });
        }
        currentPlanList = Array.from(planSet).sort();
        planSelect.innerHTML = '<option value="">-- 显示所有 --</option>';
        currentPlanList.forEach(plan => {
            const option = document.createElement('option');
            option.value = plan; option.textContent = plan;
            planSelect.appendChild(option);
        });
        const showPlanSelect = currentPlanList.length > 0;
        planSelect.style.display = showPlanSelect ? 'inline-block' : 'none';
        document.querySelector('.plan-label').style.display = showPlanSelect ? 'inline-block' : 'none';
    }

    // --- Function to Fetch Data from Backend API ---
    async function fetchData() {
        const selectedApp = appSelect.value;
        const selectedPlan = planSelect.value;
        const apiUrl = `/api/prices?app=${encodeURIComponent(selectedApp)}${selectedPlan ? '&plan=' + encodeURIComponent(selectedPlan) : ''}`;

        loadingDiv.style.display = 'block';
        errorDiv.style.display = 'none'; errorDiv.textContent = '加载数据时出错。';
        priceTableBody.innerHTML = '';
        lastUpdatedDiv.style.display = 'none'; lastUpdatedDiv.textContent = '';

        try {
            const response = await fetch(apiUrl);
            if (!response.ok) {
                let errorMsg = `HTTP 错误: ${response.status}`;
                try { const errData = await response.json(); errorMsg = errData.error || errorMsg; }
                catch (e) { /* Ignore */ }
                throw new Error(errorMsg);
            }
            const data = await response.json();

            if (!selectedPlan) { populatePlanOptions(data.prices); }

            // Populate Table
            if (data.prices && data.prices.length > 0) {
                data.prices.forEach(item => {
                    const row = priceTableBody.insertRow();
                    let displayPlan = item.plan_name || "N/A";
                    // Keep plan inference logic if needed
                    if (displayPlan === "N/A" && item.app_name === 'iCloud+' && item.region === 'US') {
                        const priceMap = {0.99: '50GB', 2.99: '200GB', 9.99: '2TB', 29.99: '6TB', 59.99: '12TB'};
                         if (item.price in priceMap) displayPlan = priceMap[item.price];
                     }

                    // --- Robust Price Formatting ---
                    let localPriceDisplay = 'N/A';
                    // Check if item.price exists and is potentially numeric
                    if (item.price !== null && item.price !== undefined) {
                        // Attempt to convert to number, then format
                        const priceNum = Number(item.price); // Handles both numbers and numeric strings
                        if (!isNaN(priceNum)) { // Check if conversion was successful
                            localPriceDisplay = `${priceNum.toFixed(2)} ${item.currency || ''}`;
                        } else {
                            // If it exists but isn't a number, display as is (or show error)
                            localPriceDisplay = `${item.price} ${item.currency || ''}`; // Display raw value
                            console.warn("item.price was not null/undefined but isNaN:", item.price);
                        }
                    }

                    let cnyPriceDisplay = 'N/A';
                    // Check if item.price_cny exists and is potentially numeric
                    if (item.price_cny !== null && item.price_cny !== undefined) {
                        const priceCnyNum = Number(item.price_cny);
                        if (!isNaN(priceCnyNum)) {
                            cnyPriceDisplay = `¥ ${priceCnyNum.toFixed(2)}`;
                        } else {
                            cnyPriceDisplay = `¥ ${item.price_cny}`; // Display raw value if not a number
                            console.warn("item.price_cny was not null/undefined but isNaN:", item.price_cny);
                        }
                    }
                    // --- End Robust Price Formatting ---


                    // --- Modified Cell for Region ---
                    const regionCell = row.insertCell();
                    const countryName = item.country_name || item.region || '未知'; // Fallback
                    const regionCode = item.region || 'N/A';
                    // Display Name (Code) format, use only code if name is same as code or unknown
                    regionCell.textContent = (countryName && countryName !== regionCode) ? `${countryName} (${regionCode})` : regionCode;
                    // --- End Modification ---

                    row.insertCell().textContent = displayPlan;
                    row.insertCell().textContent = localPriceDisplay; // Use formatted string
                    row.insertCell().textContent = cnyPriceDisplay; // Use formatted string
                });

                if (data.last_updated && data.last_updated !== "N/A" && data.last_updated !== "Never") {
                     try {
                        lastUpdatedDiv.textContent = `数据更新于: ${new Date(data.last_updated).toLocaleString()}`;
                        lastUpdatedDiv.style.display = 'block';
                     } catch (e) {
                         lastUpdatedDiv.textContent = `数据更新时间戳格式无法解析: ${data.last_updated}`;
                         lastUpdatedDiv.style.display = 'block';
                     }
                } else {
                    lastUpdatedDiv.textContent = '数据更新时间未知。';
                    lastUpdatedDiv.style.display = 'block';
                }

            } else {
                const row = priceTableBody.insertRow();
                const cell = row.insertCell();
                cell.colSpan = 4;
                cell.textContent = '未找到符合条件的价格信息。服务可能在此地区不可用或暂无数据。';
                cell.style.textAlign = 'center'; cell.style.padding = '20px'; cell.style.color = '#6c757d';
            }
        } catch (error) {
            console.error('Fetch error:', error);
            errorDiv.textContent = `加载数据时出错: ${error.message}`;
            errorDiv.style.display = 'block';
        } finally {
            loadingDiv.style.display = 'none';
        }
    }

    // --- Event Listeners & Initial Load ---
    appSelect.addEventListener('change', () => {
        planSelect.innerHTML = '<option value="">-- 显示所有 --</option>';
        fetchData();
    });
    planSelect.addEventListener('change', fetchData);
    fetchData(); // Initial load

});
