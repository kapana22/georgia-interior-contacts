// Broad sectors from the source's NACE Rev. 2 code. Narrow sectors are used
// only where the published code supports them (e.g. 71.11 architecture).
export const activityCategories={
  trade:'ვაჭრობა',transport:'ტრანსპორტი და ლოგისტიკა',hospitality:'სასტუმროები და ტურიზმი',
  education:'განათლება',finance:'ფინანსები და დაზღვევა','real-estate':'უძრავი ქონების მომსახურება',
  'arts-sport':'კულტურა და სპორტი',design:'დიზაინი',engineering:'საინჟინრო მომსახურება',
  environment:'წყალი, ნარჩენები და გარემო','public-services':'საჯარო და სოციალური მომსახურება'
};
export function activityCategory(record){
  const code=String(record.activity_code||'').trim();
  if(!/^\d{2}(?:\.\d{1,2}(?:\.\d)?)?$/.test(code))return null;
  const division=Number(code.slice(0,2));let category;
  if(code.startsWith('41.10'))category='development';
  else if(code.startsWith('71.11'))category='architecture';
  else if(code.startsWith('71.12'))category='engineering';
  else if(code.startsWith('74.10'))category='design';
  else if(code.startsWith('96.02'))category='beauty';
  else if(division>=1&&division<=3)category='agriculture';
  else if(division>=5&&division<=33)category=division>=10&&division<=12?'food-production':'industry';
  else if(division===35)category='energy';
  else if(division>=36&&division<=39)category='environment';
  else if(division>=41&&division<=43)category='construction';
  else if(division===45)category='auto';
  else if(division===46||division===47)category='trade';
  else if(division>=49&&division<=53)category='transport';
  else if(division===55||division===79)category='hospitality';
  else if(division===56)category='food';
  else if(division>=58&&division<=60||division===73)category='marketing';
  else if(division>=61&&division<=63)category='technology';
  else if(division>=64&&division<=66)category='finance';
  else if(division===68)category='real-estate';
  else if(division>=69&&division<=82)category='business-services';
  else if(division===85)category='education';
  else if(division===86)category='healthcare';
  else if(division===84||division===87||division===88)category='public-services';
  else if(division>=90&&division<=93)category='arts-sport';
  else if(division>=94&&division<=99)category='other';
  if(!category)return null;
  const official=/^https:\/\/br\.geostat\.ge\//.test(record.activity_source_url||'');
  return {category,status:official?'confirmed':'candidate',reason:`${official?'საქსტატი · ':''}${code} · ${record.activity_name||'საქმიანობის კოდი'}`};
}
